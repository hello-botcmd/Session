import re
from datetime import datetime, timezone

HEX_RE = re.compile(r"^[0-9a-fA-F]{100,}$")


def h(value) -> str:
    """HTML-escape text for Telegram HTML messages."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def is_hex(s: str) -> bool:
    return bool(HEX_RE.match(s.strip()))


def fmt_phone(p: str) -> str:
    return f"+{p}" if p else "Unknown"


def fmt_ago(dt) -> str:
    if not dt:
        return "Unknown"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = (datetime.now(timezone.utc) - dt).total_seconds()

    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86400:
        return f"{int(diff // 3600)}h ago"

    return f"{int(diff // 86400)}d ago"


def fmt_device(a) -> str:
    cur = "  ⚡  (current)" if getattr(a, "current", False) else ""

    model = a.device_model or "Unknown browser"
    plat = a.platform or "Unknown"

    app = (
        f"{a.app_name} v{a.app_version}"
        if getattr(a, "app_version", "")
        else (a.app_name or "Unknown")
    )

    ip = a.ip or ""

    region = ", ".join(
        x for x in (a.country, a.region) if x
    ) or "Unknown"

    return (
        f"📱 DEVICE SPECIFICATIONS{cur}\n"
        f"├─ Model    : {model}\n"
        f"├─ Platform : {plat}\n"
        f"├─ App      : {app}\n"
        f"├─ IP       : {ip}\n"
        f"├─ Region   : {region}\n"
        f"├─ Active   : {fmt_ago(a.date_active)}\n"
        f"└─ Created  : {fmt_ago(a.date_created)}"
    )


def fmt_account_card(info: dict, title: str = "Account Verified") -> str:
    """Format verified Telegram account information."""

    phone = fmt_phone(info.get("phone"))
    name = info.get("name") or "Unknown"
    user_id = info.get("user_id") or "Unknown"
    spam = info.get("spam") or "Unknown"
    devices = info.get("device_count", 0)

    return (
        f"✅ <b>{h(title)}</b>\n\n"
        f"📱 <b>Phone:</b> <code>{h(phone)}</code>\n"
        f"👤 <b>Name:</b> {h(name)}\n"
        f"🆔 <b>User ID:</b> <code>{h(user_id)}</code>\n"
        f"⚠️ <b>Spam status:</b> {h(spam)}\n"
        f"📲 <b>Devices:</b> {h(devices)}"
    )
