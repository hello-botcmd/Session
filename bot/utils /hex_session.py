
"""Convert a panel hex auth_key into a real Telethon StringSession.

Never pack the session binary by hand. Telethon's official layout is:

    CURRENT_VERSION ('1') + urlsafe_b64( struct.pack('>B{n}sH256s', dc, ip, port, key) )

Hand-rolling that (version 2, 4-byte dc_id, string IP) produces a string that
looks valid but never authorizes. Other bots work because they use Pyrogram /
GramJS, which accept a raw 256-byte key. We do the same thing the Telethon way:

    session = StringSession()
    session.set_dc(dc, ip, 443)
    session.auth_key = AuthKey(raw_256)
    session.save()
"""

from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from config import API_HASH, API_ID

log = logging.getLogger(__name__)

# Official production IPv4 DCs used by Telethon itself
DC_IPV4 = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

_cache: dict[str, str] = {}


def _is_hex(s: str) -> bool:
    s = s.strip()
    return bool(s) and len(s) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in s)


def _looks_like_telethon(s: str) -> bool:
    """Telethon strings start with version char '1' and are urlsafe base64."""
    if not s or s[0] != "1" or len(s) < 50:
        return False
    try:
        StringSession(s)
        return True
    except Exception:
        return False


def make_string(dc_id: int, auth_key: bytes) -> str:
    """Let Telethon encode dc + ip + port + key. This is the only correct way."""
    if dc_id not in DC_IPV4:
        raise ValueError(f"Unknown DC {dc_id}")
    if len(auth_key) != 256:
        raise ValueError(f"auth_key must be 256 bytes, got {len(auth_key)}")
    session = StringSession()
    session.set_dc(dc_id, DC_IPV4[dc_id], 443)
    session.auth_key = AuthKey(auth_key)
    saved = session.save()
    if not saved:
        raise ValueError("Telethon refused to save the session (empty auth_key?)")
    return saved


def _extract_keys(raw: bytes) -> list[tuple[int | None, bytes]]:
    """Return (dc_hint, auth_key) candidates from raw panel bytes."""
    out: list[tuple[int | None, bytes]] = []

    # A) bare 256-byte auth_key  -- your panel
    if len(raw) == 256:
        out.append((None, raw))

    # B) 1-byte dc + 256-byte key
    elif len(raw) == 257:
        dc = raw[0]
        out.append((dc if dc in DC_IPV4 else None, raw[1:]))

    # C) 4-byte dc + 256-byte key (some panels)
    elif len(raw) == 260:
        for endian in ("big", "little"):
            dc = int.from_bytes(raw[:4], endian)
            if dc in DC_IPV4:
                out.append((dc, raw[4:]))
        if not out:
            out.append((None, raw[4:]))

    # D) Pyrogram raw: dc_id LE (4) + test_mode (1) + auth_key (256)
    elif len(raw) == 261:
        dc = int.from_bytes(raw[:4], "little")
        out.append((dc if dc in DC_IPV4 else None, raw[5:]))

    # E) 2-byte dc + 256-byte key
    elif len(raw) == 258:
        for endian in ("big", "little"):
            dc = int.from_bytes(raw[:2], endian)
            if dc in DC_IPV4:
                out.append((dc, raw[2:]))
        if not out:
            out.append((None, raw[2:]))

    else:
        raise ValueError(
            f"Unsupported hex length {len(raw)} bytes "
            f"({len(raw) * 2} hex chars). Expected 256/257/258/260/261."
        )

    return out


async def _authorized(session_str: str) -> str | None:
    """Connect with a candidate string. Return the (possibly migrated) saved string."""
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=12)
        if await client.is_user_authorized():
            # save AFTER connect so a DC migrate is reflected
            return client.session.save() or session_str
        return None
    except Exception as exc:
        log.debug("DC probe failed: %s", exc)
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _probe(auth_key: bytes, dc_hint: int | None = None) -> str:
    """Try the hinted DC first, then the rest. Sequential to avoid flood."""
    order = list(DC_IPV4)
    if dc_hint in DC_IPV4:
        order.remove(dc_hint)
        order.insert(0, dc_hint)

    last_ok_fail = "no DC accepted this auth_key"
    for dc in order:
        try:
            candidate = make_string(dc, auth_key)
        except Exception as exc:
            last_ok_fail = str(exc)
            continue
        saved = await _authorized(candidate)
        if saved:
            log.info("hex auth_key authorized on DC%s", dc)
            return saved
        last_ok_fail = f"DC{dc} connected but session is not authorized"

    raise ValueError(
        "Could not authorize this hex on any Telegram DC (1-5). "
        f"Last: {last_ok_fail}. Key is expired/revoked, or not a Telegram auth_key."
    )


async def hex_to_session_string(raw_input: str) -> str:
    """Accept a Telethon string OR a panel hex. Always return a working StringSession."""
    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise ValueError("Empty session input")

    if raw_input in _cache:
        return _cache[raw_input]

    if _looks_like_telethon(raw_input):
        _cache[raw_input] = raw_input
        return raw_input

    # Some panels wrap the Telethon string as hex(utf8(string))
    if _is_hex(raw_input):
        try:
            decoded = bytes.fromhex(raw_input)
            as_text = decoded.decode("ascii", errors="strict")
            if _looks_like_telethon(as_text):
                _cache[raw_input] = as_text
                return as_text
        except (ValueError, UnicodeDecodeError):
            pass

        key_candidates = _extract_keys(bytes.fromhex(raw_input))
        last_err: Exception | None = None
        for dc_hint, key in key_candidates:
            try:
                saved = await _probe(key, dc_hint)
                _cache[raw_input] = saved
                return saved
            except Exception as exc:
                last_err = exc
        raise last_err or ValueError("Unsupported hex format")

    # Last chance: maybe it's a Telethon string that decode() is picky about
    try:
        StringSession(raw_input)
        _cache[raw_input] = raw_input
        return raw_input
    except Exception:
        raise ValueError(
            "Input is neither a Telethon StringSession nor a hex auth_key"
        ) from None

