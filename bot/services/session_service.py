from __future__ import annotations

import asyncio
import imaplib
import re
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import functions, types

from bot.utils.hex_session import connect_from_raw, hex_to_session_string
from config import API_HASH, API_ID

OTP_RE = re.compile(r"\b(\d{5,6})\b")
SPAMBOT = 178220800
_SESSION_CACHE: dict[str, str] = {}


async def _sleep_flood(exc: FloodWaitError) -> None:
    await asyncio.sleep(int(getattr(exc, "seconds", 1)) + 1)


async def get_session_string(raw: str) -> str:
    raw = (raw or "").strip()
    if raw in _SESSION_CACHE:
        return _SESSION_CACHE[raw]
    converted = await hex_to_session_string(raw)
    from bot.utils import hex_session as _hs

    cleaned = _hs._clean(raw)
    if cleaned in _hs._cache:
        _SESSION_CACHE[raw] = _hs._cache[cleaned]
        return _SESSION_CACHE[raw]
    return converted


async def make_client(raw: str) -> TelegramClient:
    return await connect_from_raw(raw)


async def _connect(client: TelegramClient) -> None:
    if not client.is_connected():
        await asyncio.wait_for(client.connect(), timeout=15)
    try:
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=10)
    except Exception as exc:
        raise ValueError(f"Authorization check failed: {type(exc).__name__}: {exc}") from exc
    if not authorized:
        raise ValueError("Connected, but Telegram rejected the auth key (dead / revoked / wrong DC).")


def _auth_to_dict(a) -> dict:
    return {
        "hash": a.hash,
        "current": bool(a.current),
        "device": a.device_model or "Unknown",
        "app": a.app_name or "",
        "app_version": a.app_version or "",
        "platform": a.platform or "",
        "ip": a.ip or "",
        "country": a.country or "",
        "region": getattr(a, "region", "") or "",
        "date": getattr(a, "date_active", None) or getattr(a, "date_created", None),
        "date_active": getattr(a, "date_active", None),
        "date_created": getattr(a, "date_created", None),
    }


async def list_devices(client: TelegramClient) -> list[dict]:
    await _connect(client)
    try:
        auths = await client(functions.account.GetAuthorizationsRequest())
    except FloodWaitError as exc:
        await _sleep_flood(exc)
        auths = await client(functions.account.GetAuthorizationsRequest())
    return [_auth_to_dict(a) for a in auths.authorizations]


async def account_summary(client: TelegramClient) -> dict:
    await _connect(client)
    me = await client.get_me()
    if me is None:
        raise ValueError("get_me() returned None — session is dead")
    name = " ".join(p for p in (me.first_name, me.last_name) if p).strip() or "Unknown"
    devices = []
    try:
        devices = await list_devices(client)
    except Exception:
        devices = []
    return {
        "phone": me.phone or "hidden",
        "name": name,
        "user_id": me.id,
        "username": me.username or "",
        "devices": devices,
        "device_count": len(devices),
        "spam": "",
        "spam_detail": "",
        "session_string": client.session.save() if client.session else "",
    }


async def spam_status(client: TelegramClient) -> tuple[str, str]:
    """Ask @SpamBot. Never probe by messaging Saved Messages."""
    await _connect(client)
    try:
        await client.send_message(SPAMBOT, "/start")
    except FloodWaitError as exc:
        await _sleep_flood(exc)
        await client.send_message(SPAMBOT, "/start")
    except Exception as exc:
        return "Unknown", f"Could not reach SpamBot: {exc}"

    deadline = asyncio.get_running_loop().time() + 12
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(1.2)
        try:
            async for msg in client.iter_messages(SPAMBOT, limit=6):
                if not msg or msg.out or not msg.message:
                    continue
                last = msg.message.strip()
                break
        except Exception:
            continue
        if last:
            break

    if not last:
        return "Unknown", "SpamBot did not reply in time."

    low = last.lower()
    if "good news" in low or "no limits" in low:
        short = "Clean"
    elif "banned" in low or "deactivated" in low:
        short = "Banned"
    elif "limited" in low or "restrict" in low or "spam" in low:
        short = "Restricted"
    else:
        short = "See details"
    return short, last


async def verify(raw: str) -> tuple[dict, TelegramClient]:
    client = await make_client(raw)
    try:
        info = await account_summary(client)
        try:
            info["spam"], info["spam_detail"] = await spam_status(client)
        except Exception as exc:
            info["spam"], info["spam_detail"] = "Unknown", str(exc)
        session_string = info.get("session_string") or ""
        if session_string:
            _SESSION_CACHE[(raw or "").strip()] = session_string
        return info, client
    except Exception:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise


async def terminate_hash(client: TelegramClient, auth_hash: int) -> None:
    await _connect(client)
    await client(functions.account.ResetAuthorizationRequest(hash=int(auth_hash)))


async def terminate_device(client: TelegramClient, auth_hash: int) -> None:
    await terminate_hash(client, auth_hash)


async def terminate_all_others(client: TelegramClient) -> int:
    devices = await list_devices(client)
    removed = 0
    for item in devices:
        if item.get("current") or not item.get("hash"):
            continue
        try:
            await terminate_hash(client, item["hash"])
            removed += 1
        except FloodWaitError as exc:
            await _sleep_flood(exc)
        except Exception:
            pass
    return removed


async def revoke_session(client: TelegramClient) -> None:
    await _connect(client)
    await client.log_out()


async def reset_authorizations(client: TelegramClient) -> None:
    await _connect(client)
    await client(functions.auth.ResetAuthorizationsRequest())


async def fetch_otp(client: TelegramClient, timeout: int = 25) -> dict | None:
    await _connect(client)

    async def _scan() -> dict | None:
        async for msg in client.iter_messages(777000, limit=8):
            if not msg or not msg.message:
                continue
            found = OTP_RE.search(msg.message)
            if found:
                return {
                    "date": msg.date or datetime.now(timezone.utc),
                    "code": found.group(1),
                    "chat": "Telegram (777000)",
                }
        async for dialog in client.iter_dialogs(limit=15):
            if dialog.id == 777000:
                continue
            async for msg in client.iter_messages(dialog.id, limit=3):
                if not msg or not msg.message:
                    continue
                body = msg.message.lower()
                if "login code" in body or "код" in body:
                    found = OTP_RE.search(msg.message)
                    if found:
                        return {
                            "date": msg.date or datetime.now(timezone.utc),
                            "code": found.group(1),
                            "chat": dialog.name or str(dialog.id),
                        }
        return None

    result = await _scan()
    if result:
        return result
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(2)
        result = await _scan()
        if result:
            return result
    return None


async def read_otp(client: TelegramClient, timeout: int = 25):
    result = await fetch_otp(client, timeout=timeout)
    if not result:
        return None
    return result["date"], result["code"], result["chat"]


async def clear_all(client: TelegramClient, update_stage=None) -> dict:
    await _connect(client)
    stats = {"contacts": 0, "dialogs": 0, "groups": 0, "channels": 0}

    if update_stage:
        await update_stage("contacts")
    try:
        result = await client(functions.contacts.GetContactsRequest(hash=0))
        users = list(getattr(result, "users", []) or [])
        if users:
            await client(functions.contacts.DeleteContactsRequest(id=users))
            stats["contacts"] = len(users)
    except FloodWaitError as exc:
        await _sleep_flood(exc)
    except Exception:
        pass

    if update_stage:
        await update_stage("dialogs")

    me = await client.get_me()
    async for dialog in client.iter_dialogs():
        try:
            if me and dialog.id == me.id:
                continue
            if dialog.is_channel:
                await client.delete_dialog(dialog.entity)
                if getattr(dialog.entity, "megagroup", False) or dialog.is_group:
                    stats["groups"] += 1
                else:
                    stats["channels"] += 1
            elif dialog.is_group:
                await client.delete_dialog(dialog.entity)
                stats["groups"] += 1
            else:
                await client.delete_dialog(dialog.entity, revoke=True)
                stats["dialogs"] += 1
        except FloodWaitError as exc:
            await _sleep_flood(exc)
        except Exception:
            continue
    return stats


async def change_email(client: TelegramClient, email: str, app_password: str) -> None:
    await _connect(client)
    if not email or not app_password:
        raise ValueError("Add your mailbox first with /addmail email ---- app_password")

    await client(
        functions.account.SendVerifyEmailCodeRequest(
            purpose=types.EmailVerifyPurposeLoginChange(),
            email=email,
        )
    )
    code = await _wait_imap_code(email, app_password, timeout=90)
    if not code:
        raise ValueError(f"No verification code arrived in {email}")

    await client(
        functions.account.VerifyEmailRequest(
            purpose=types.EmailVerifyPurposeLoginChange(),
            verification=types.EmailVerificationCode(code=code),
        )
    )


def _imap_once(email: str, app_password: str, seen: set[bytes]) -> str | None:
    host = "imap.gmail.com"
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        host = "outlook.office365.com"
    elif domain in {"yahoo.com", "ymail.com"}:
        host = "imap.mail.yahoo.com"

    mail = imaplib.IMAP4_SSL(host, 993)
    try:
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
            found = OTP_RE.search(text)
            if found and ("telegram" in text.lower() or "verify" in text.lower()):
                return found.group(1)
        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass


async def _wait_imap_code(email: str, app_password: str, timeout: int = 90) -> str | None:
    deadline = asyncio.get_running_loop().time() + timeout
    seen: set[bytes] = set()
    while asyncio.get_running_loop().time() < deadline:
        try:
            code = await asyncio.to_thread(_imap_once, email, app_password, seen)
            if code:
                return code
        except Exception:
            pass
        await asyncio.sleep(4)
    return None
