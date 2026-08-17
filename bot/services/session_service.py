import imaplib
import re
import time

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions

from config import API_HASH, API_ID

OTP_RE = re.compile(r"(?:code|otp|verification)[^\d]{0,30}?(\d{5,6})", re.I)
CODE_RE = re.compile(r"\b\d{5,6}\b")


# ---------------------------------------------------------------- connection
async def make_client(hex_str: str) -> TelegramClient:
    """Connect a Telethon client from a session hex string."""
    client = TelegramClient(StringSession(hex_str), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        raise ValueError("Session is expired, revoked or invalid")
    return client


async def account_summary(client: TelegramClient) -> dict:
    """Fetch phone, name, user id, device count and spam status."""
    me = await client.get_me()
    devices = []
    try:
        res = await client(functions.account.GetAuthorizationsRequest())
        devices = res.authorizations
    except Exception:
        pass
    return {
        "phone": me.phone,
        "name": f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown",
        "user_id": me.id,
        "devices": len(devices),
        "spam": "Spam" if getattr(me, "restricted", False) else "Non-Spam",
    }


async def verify(hex_str: str):
    """Verify a hex + run a spam check. Returns (info_dict, connected_client)."""
    client = await make_client(hex_str)
    try:
        info = await account_summary(client)
        spam = info["spam"] == "Spam"

        # Restricted accounts usually fail even GetAccountTtl
        try:
            await client(functions.account.GetAccountTtlRequest())
        except Exception:
            spam = True

        # Probe: a truly restricted account can't even message Saved Messages
        if not spam:
            try:
                msg = await client.send_message("me", ".")
                spam = False
                try:
                    await client.delete_messages("me", [msg.id])
                except Exception:
                    pass
            except Exception:
                spam = True

        info["spam"] = "Spam" if spam else "Non-Spam"
        return info, client
    except Exception:
        await client.disconnect()
        raise


# ------------------------------------------------------------------- devices
async def list_devices(client: TelegramClient):
    res = await client(functions.account.GetAuthorizationsRequest())
    return res.authorizations


async def terminate_device(client: TelegramClient, hash_: int):
    await client(functions.account.DeleteAuthorizationsRequest(hashes=[hash_]))


async def revoke_session(client: TelegramClient):
    """Log out this session permanently."""
    try:
        await client.log_out()
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ------------------------------------------------------------------ clear all
async def clear_all(client: TelegramClient, progress=None) -> dict:
    """Wipe contacts, DMs, groups and channels from the account."""
    stats = {"contacts": 0, "dialogs": 0, "groups": 0, "channels": 0}

    # 1) Contacts
    if progress:
        await progress("contacts")
    try:
        contacts = await client(functions.contacts.GetContactsRequest(hash=0))
        for u in contacts.users:
            try:
                await client(functions.contacts.DeleteContactsRequest(id=[u.id]))
                stats["contacts"] += 1
            except Exception:
                pass
    except Exception:
        pass

    # 2) Everything else
    if progress:
        await progress("dialogs")
    async for dialog in client.iter_dialogs():
        if dialog.is_self:  # never touch Saved Messages
            continue
        try:
            if dialog.is_user:
                await client(
                    functions.messages.DeleteHistoryRequest(
                        peer=dialog.id, revoke=True, max_id=0
                    )
                )
                stats["dialogs"] += 1
            elif dialog.is_group:
                try:
                    await client(
                        functions.messages.DeleteChatRequest(chat_id=abs(dialog.id))
                    )
                except Exception:
                    try:
                        await client(functions.channels.LeaveChannelRequest(dialog.entity))
                    except Exception:
                        try:
                            await client(
                                functions.messages.DeleteChatUserRequest(
                                    chat_id=abs(dialog.id), user_id="me"
                                )
                            )
                        except Exception:
                            pass
                stats["groups"] += 1
            elif dialog.is_channel:
                try:
                    await client(functions.channels.DeleteChannelRequest(dialog.entity))
                except Exception:
                    try:
                        await client(functions.channels.LeaveChannelRequest(dialog.entity))
                    except Exception:
                        pass
                stats["channels"] += 1
        except Exception:
            continue
    return stats


# ------------------------------------------------------------------------ OTP
async def read_otp(client: TelegramClient, limit_dialogs: int = 15, limit_msgs: int = 40):
    """Scan recent chats for the latest Telegram login code."""
    found = []
    try:
        async for dialog in client.iter_dialogs(limit=limit_dialogs):
            try:
                async for msg in client.iter_messages(dialog.id, limit=limit_msgs):
                    if not msg.message:
                        continue
                    m = OTP_RE.search(msg.message)
                    if m:
                        found.append(
                            (msg.date, m.group(1), dialog.name or str(dialog.id))
                        )
                        break
            except Exception:
                continue
    except Exception:
        pass
    if not found:
        return None
    found.sort(key=lambda x: x[0], reverse=True)
    return found[0]


# ------------------------------------------------------------------ change email
def _imap_host(email: str):
    domain = email.split("@")[-1].lower()
    return {
        "gmail.com": "imap.gmail.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "yahoo.com": "imap.mail.yahoo.com",
    }.get(domain)


def _read_email_code(email: str, app_password: str, attempts: int = 6, delay: int = 5):
    """Fetch the verification code from the new email inbox via IMAP."""
    host = _imap_host(email)
    if not host:
        return None
    for _ in range(attempts):
        try:
            conn = imaplib.IMAP4_SSL(host)
            conn.login(email, app_password)
            conn.select("INBOX")
            status, data = conn.search(None, "ALL")
            if status == "OK":
                ids = data[0].split()[-5:]
                for i in reversed(ids):
                    _, msg_data = conn.fetch(i, "(BODY.PEEK[TEXT])")
                    body = msg_data[0][1].decode(errors="ignore")
                    m = CODE_RE.search(body)
                    if m:
                        conn.logout()
                        return m.group(1)
            conn.logout()
        except Exception:
            pass
        time.sleep(delay)
    return None


async def change_email(client: TelegramClient, email: str, app_password: str = None):
    """Change the account's login email (verify-code flow)."""
    try:
        from telethon.tl.functions.account import (  # layer 158+
            SendVerifyEmailCodeRequest,
            VerifyEmailRequest,
        )
    except ImportError:
        raise RuntimeError(
            "Your Telethon version doesn't support the email API. "
            "Upgrade: pip install -U telethon"
        )

    try:
        await client(SendVerifyEmailCodeRequest(email=email))
    except Exception as e:
        raise RuntimeError(f"Failed to send verification code: {e}")

    code = _read_email_code(email, app_password) if app_password else None
    if not code:  # fallback: code might arrive as a Telegram message
        try:
            otp = await read_otp(client)
            if otp:
                code = otp[1]
        except Exception:
            pass
    if not code:
        raise RuntimeError("Could not read the verification code automatically.")

    try:
        await client(VerifyEmailRequest(email=email, code=code))
    except Exception as e:
        raise RuntimeError(f"Email verification failed: {e}")
    return True
