 from __future__ import annotations

import html
import re
from datetime import datetime, timezone

HEX_RE = re.compile(r"^[0-9a-fA-F]{100,}$")
SESSION_STR_RE = re.compile(r"^[A-Za-z0-9_\-]{80,}$")


def is_session_input(s: str) -> bool:
    text = (s or "").strip()
    if not text:
        return False
    if HEX_RE.match(text):
        return True
    if text.startswith("1") and len(text) > 80:
        return True
    return bool(SESSION_STR_RE.match(text))


def is_hex(s: str) -> bool:
    return is_session_input(s)


def h(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def fmt_phone(p: str | None) -> str:
    if not p or str(p).lower() in {"hidden", "unknown", "none"}:
        return "Hidden"
    text = str(p).strip()
    if text.startswith("+"):
        return text
    return f"+{text}"


def fmt_ago(dt) -> str:
    if not dt:
        return "Unknown"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
        if diff < 0:
            diff = 0
        if diff < 60:
            return "just now"
        if diff < 3600:
            return f"{int(diff // 60)}m ago"
        if diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return f"{int(diff // 86400)}d ago"
    return str(dt)


def _field(obj, *names, default=""):
    if isinstance(obj, dict):
        for name in names:
            value = obj.get(name)
            if value not in (None, ""):
                return value
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value not in (None, ""):
            return value
    return default


def device_hash(obj) -> int:
    try:
        return int(_field(obj, "hash", default=0) or 0)
    except (TypeError, ValueError):
        return 0


def is_current_device(obj) -> bool:
    return bool(_field(obj, "current", default=False))


def fmt_device(a) -> str:
    current = "  ⚡ current" if is_current_device(a) else ""
    model = _field(a, "device", "device_model", default="Unknown")
    plat = _field(a, "platform", default="Unknown")
    app = _field(a, "app", "app_name", default="Unknown")
    version = _field(a, "app_version", default="")
    if version and version not in str(app):
        app = f"{app} v{version}"
    ip = _field(a, "ip", default="") or "—"
    country = _field(a, "country", default="")
    region = _field(a, "region", default="")
    place = ", ".join(x for x in (country, region) if x and x not in (country and region and country))
    if country and region:
        if str(region).lower() in str(country).lower():
            place = country
        elif str(country).lower() in str(region).lower():
            place = region
        else:
            place = f"{country}"
            if region not in str(country):
                place = f"{region}, {country}" if "," not in str(country) else country
    place = place or "Unknown"
    active = fmt_ago(_field(a, "date_active", "date", default=None))
    created = fmt_ago(_field(a, "date_created", "date", default=None))
    return (
        f"📱 DEVICE SPECIFICATIONS{current}\n"
        f"├─ Model    : {h(model)}\n"
        f"├─ Platform : {h(plat)}\n"
        f"├─ App      : {h(app)}\n"
        f"├─ IP       : {h(ip)}\n"
        f"├─ Region   : {h(place)}\n"
        f"├─ Active   : {h(active)}\n"
        f"└─ Created  : {h(created)}"
    )


def device_count(devices) -> int:
    if isinstance(devices, list):
        return len(devices)
    try:
        return int(devices or 0)
    except (TypeError, ValueError):
        return 0


def fmt_device_list(devices: list, limit: int = 5) -> str:
    if not devices:
        return "No sessions returned."
    blocks = []
    for idx, item in enumerate(devices[:limit], start=1):
        blocks.append(f"<b>Device {idx}/{len(devices)}</b>\n{fmt_device(item)}")
    extra = len(devices) - limit
    if extra > 0:
        blocks.append(f"… +{extra} more in Device Dashboard")
    return "\n\n".join(blocks)


def fmt_account_card(info: dict, title: str = "Account Verified") -> str:
    devices = info.get("devices")
    count = info.get("device_count")
    if count is None:
        count = device_count(devices)
    spam = info.get("spam") or "Unknown"
    detail = (info.get("spam_detail") or "").strip()
    text = (
        f"✅ <b>{h(title)}</b>\n\n"
        f"📱 Phone   : <code>{h(fmt_phone(info.get('phone')))}</code>\n"
        f"👤 Name    : {h(info.get('name') or 'Unknown')}\n"
        f"🆔 User ID : <code>{h(info.get('user_id') or 'Unknown')}</code>\n"
        f"📟 Devices : {count} connected\n"
        f"⚠️ Status  : {h(spam)}"
    )
    if detail:
        short = detail if len(detail) <= 280 else detail[:277] + "..."
        text += f"\n\n<b>SpamBot</b>\n{h(short)}"
    if isinstance(devices, list) and devices:
        text += "\n\n" + fmt_device_list(devices)
    return text
