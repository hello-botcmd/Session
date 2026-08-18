"""Telegram session operations used by Manage / Guard / My Accounts."""

from __future__ import annotations

import asyncio
import imaplib
import re
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions, types

from bot.utils.hex_session import hex_to_session_string
from config import API_HASH, API_ID

OTP_RE = re.compile(r"\b(\d{5,6})\b")
_SESSION_CACHE: dict[str, str] = {}


async def get_session_string(raw: str) -> str:
    raw = (raw or "").strip()
    if raw in _SESSION_CACHE:
        return _SESSION_CACHE[raw]
    converted = await hex_to_session_string(raw)
    _SESSION_CACHE[raw] = converted
    return converted

async def make_client(raw: str) -> TelegramClient:
    session_string = await get_session_string(raw)
    if not session_string or not session_string.startswith("1"):
        raise ValueError(
            "Session conversion produced an invalid Telethon string. "
            "The hex format is not supported or conversion failed."
        )
    return TelegramClient(StringSession(session_string), API_ID, API_HASH)


async def _connect(client: TelegramClient) -> None:
    if not client.is_connected():
        await asyncio.wait_for(client.connect(), timeout=15)
    try:
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10)
    except Exception as exc:
        raise ValueError(f"Authorization check failed: {type(exc).__name__}: {exc}") from exc
    if not authorized:
        raise ValueError(
            "Connected, but Telegram rejected the auth key "
            "(dead / revoked / wrong DC)."
        )
    


async def verify(raw: str) -> tuple[dict, TelegramClient]:
    """Convert hex, connect, pull account info. Caller must disconnect client."""
    client = await make_client(raw)
    await _connect(client)

    me = await client.get_me()
    if me is None:
        await client.disconnect()
        raise ValueError("get_me() returned None — session is dead")

    name = " ".join(p for p in (me.first_name, me.last_name) if p).strip() or "Unknown"
    phone = me.phone or "hidden"

    devices = []
    try:
        auths = await client(functions.account.GetAuthorizationsRequest())
        for a in auths.authorizations:
            devices.append({
                "hash": a.hash,
                "current": bool(a.current),
                "device": a.device_model or "Unknown",
                "app": a.app_name or "",
                "platform": a.platform or "",
                "ip": a.ip or "",
                "country": a.country or "",
                "date": getattr(a, "date_active", None) or getattr(a, "date_created", None),
            })
    except Exception:
        devices = []

    spam = await _spam_status(client)

    info = {
        "phone": phone,
        "name": name,
        "user_id": me.id,
        "username": me.username or "",
        "spam": spam,
        "devices": devices,
        "session_string": await get_session_string(raw),
    }
    return info, client


async def _spam_status(client: TelegramClient) -> str:
    """Best-effort restriction check. Telegram has no public 'spam' flag."""
    try:
        me = await client.get_me()
        if getattr(me, "restricted", False):
            return "restricted"
        if getattr(me, "restriction_reason", None):
            return "restricted"
    except Exception:
        pass
    try:
        await client.send_message("me", "🛡️ session-manager probe")
        return "clean"
    except Exception as exc:
        text = str(exc).lower()
        if "banned" in text or "deactivated" in text:
            return "banned"
        if "spam" in text or "restricted" in text or "peer_flood" in text:
            return "restricted"
        return "unknown"


async def list_devices(client: TelegramClient) -> list[dict]:
    await _connect(client)
    auths = await client(functions.account.GetAuthorizationsRequest())
    out = []
    for a in auths.authorizations:
        out.append({
            "hash": a.hash,
            "current": bool(a.current),
            "device": a.device_model or "Unknown",
            "app": a.app_name or "",
            "platform": a.platform or "",
            "ip": a.ip or "",
            "country": a.country or "",
            "date": getattr(a, "date_active", None) or getattr(a, "date_created", None),
        })
    return out


async def terminate_hash(client: TelegramClient, auth_hash: int) -> None:
    await _connect(client)
    await client(functions.account.ResetAuthorizationRequest(hash=auth_hash))


async def terminate_all_others(client: TelegramClient) -> int:
    """Kick every session except the one this client is using. Returns count."""
    devices = await list_devices(client)
    removed = 0
    for d in devices:
        if d["current"] or not d["hash"]:
            continue
        try:
            await terminate_hash(client, d["hash"])
            removed += 1
        except Exception:
            pass
    return removed


async def reset_authorizations(client: TelegramClient) -> None:
    """Revoke EVERY session including this one. Client dies after this."""
    await _connect(client)
    await client(functions.auth.ResetAuthorizationsRequest())


async def fetch_otp(client: TelegramClient, timeout: int = 25) -> str | None:
    """Read the latest Telegram login code from 777000 / recent dialogs."""
    await _connect(client)

    async def _scan() -> str | None:
        async for msg in client.iter_messages(777000, limit=8):
            if not msg or not msg.message:
                continue
            m = OTP_RE.search(msg.message)
            if m:
                return m.group(1)
        async for dialog in client.iter_dialogs(limit=15):
            if dialog.id == 777000:
                continue
            async for msg in client.iter_messages(dialog.id, limit=3):
                if not msg or not msg.message:
                    continue
                if "login code" in msg.message.lower() or "код" in msg.message.lower():
                    m = OTP_RE.search(msg.message)
                    if m:
                        return m.group(1)
        return None

    code = await _scan()
    if code:
        return code

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(2)
        code = await _scan()
        if code:
            return code
    return None


async def change_email(client: TelegramClient, email: str, app_password: str) -> None:
    """Best-effort login-email change via Telegram + IMAP inbox for the code."""
    await _connect(client)
    if not email or not app_password:
        raise ValueError("GUARD_EMAIL / GUARD_EMAIL_APP_PASSWORD not configured")

    sent = await client(functions.account.SendVerifyEmailCodeRequest(
        purpose=types.EmailVerifyPurposeLoginChange(),
        email=email,
    ))
    code = await _wait_imap_code(email, app_password, timeout=90)
    if not code:
        raise ValueError(f"No verification code arrived in {email}")

    await client(functions.account.VerifyEmailRequest(
        purpose=types.EmailVerifyPurposeLoginChange(),
        verification=types.EmailVerificationCode(code=code),
    ))
    return sent


async def _wait_imap_code(email: str, app_password: str, timeout: int = 90) -> str | None:
    host = "imap.gmail.com"
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        host = "outlook.office365.com"
    elif domain in {"yahoo.com", "ymail.com"}:
        host = "imap.mail.yahoo.com"

    deadline = asyncio.get_event_loop().time() + timeout
    seen: set[bytes] = set()

    while asyncio.get_event_loop().time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL(host, 993)
            mail.login(email, app_password)
            mail.select("INBOX")
            _, data = mail.search(None, "UNSEEN")
            ids = data[0].split() if data and data[0] else []
            for mid in reversed(ids[-10:]):
                if mid in seen:
                    continue
                seen.add(mid)
                _, msg_data = mail.fetch(mid, "(RFC822)")
                body = msg_data[0][1]
                if not body:
                    continue
                text = body.decode("utf-8", errors="ignore")
                m = OTP_RE.search(text)
                if m and ("telegram" in text.lower() or "verify" in text.lower()):
                    mail.logout()
                    return m.group(1)
            mail.logout()
        except Exception:
            pass
        await asyncio.sleep(4)
    return None


def fmt_when(dt) -> str:
    if not dt:
        return "?"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return str(dt)
