"""
Convert Telegram session input into a verified Telethon StringSession.

Supported:
  - Telethon StringSession (raw or hex-encoded)
  - Pyrogram StringSession (raw or hex-encoded)
  - Telethon packed bytes as hex (263 / 275)
  - Pyrogram packed bytes as hex (271 / 258)
  - Bare 256-byte auth_key as hex
  - DC-prefixed panel layouts (257 / 258 / 260 / 261)

DC probe order for a bare key: 5 → 4 → 3 → 2 → 1 (never parallel).
A candidate is accepted only after get_me() succeeds.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
from typing import Optional

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.errors import AuthKeyDuplicatedError, AuthKeyUnregisteredError
from telethon.sessions import StringSession

from config import API_HASH, API_ID

log = logging.getLogger(__name__)

DC_INFO = {
    1: ("149.154.175.53", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),
    5: ("91.108.56.130", 443),
}

# User-requested order. Never probe two DCs at once with the same key.
DC_ORDER = (5, 4, 3, 2, 1)

CONNECT_TIMEOUT = 12
RPC_TIMEOUT = 10

_cache: dict[str, str] = {}


def _clean(value: str) -> str:
    return (
        (value or "")
        .strip()
        .replace("\r", "")
        .replace("\n", "")
        .replace(" ", "")
        .replace("\t", "")
    )


def _is_hex(value: str) -> bool:
    return (
        bool(value)
        and len(value) % 2 == 0
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


def _b64dec(value: str) -> Optional[bytes]:
    value = value.strip()
    if not value:
        return None
    for fn in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return fn(value + "=" * (-len(value) % 4))
        except Exception:
            continue
    return None


def make_string(dc_id: int, auth_key: bytes) -> str:
    if dc_id not in DC_INFO:
        raise ValueError(f"Unknown Telegram DC: {dc_id}")
    if len(auth_key) != 256:
        raise ValueError(f"auth_key must be exactly 256 bytes, got {len(auth_key)}")
    ip, port = DC_INFO[dc_id]
    session = StringSession()
    session.set_dc(dc_id, ip, port)
    session.auth_key = AuthKey(auth_key)
    result = session.save()
    if not result or result[0] != "1":
        raise ValueError("Telethon failed to serialize the session")
    return result


# ---------------------------------------------------------------------------
# Parsers — never call StringSession() on untrusted input
# ---------------------------------------------------------------------------

def _parse_telethon_packed(raw: bytes) -> Optional[tuple[int, bytes]]:
    """Telethon binary: dc(1) + ip(4|16) + port(2) + key(256)."""
    if len(raw) == 263:
        dc, _ip, _port, key = struct.unpack(">B4sH256s", raw)
    elif len(raw) == 275:
        dc, _ip, _port, key = struct.unpack(">B16sH256s", raw)
    else:
        return None
    if dc in DC_INFO and len(key) == 256:
        return dc, key
    return None


def _parse_telethon_string(value: str) -> Optional[tuple[int, bytes]]:
    if not value or value[0] != "1":
        return None
    raw = _b64dec(value[1:])
    if raw is None:
        return None
    return _parse_telethon_packed(raw)


def _parse_pyrogram_packed(raw: bytes) -> Optional[tuple[int, bytes]]:
    """
    New:  >BI?256sQ?   = dc + api_id + test + key + user_id + is_bot  (271)
    Old:  dc + test + key                                             (258)
    """
    if len(raw) >= 271:
        dc = raw[0]
        key = raw[6:262]
        if dc in DC_INFO and len(key) == 256:
            return dc, key
    if len(raw) >= 258:
        dc = raw[0]
        key = raw[2:258]
        if dc in DC_INFO and len(key) == 256:
            return dc, key
    if len(raw) >= 257:
        dc = raw[0]
        key = raw[1:257]
        if dc in DC_INFO and len(key) == 256:
            return dc, key
    return None


def _parse_pyrogram_string(value: str) -> Optional[tuple[int, bytes]]:
    payloads = [value]
    if value.startswith("1") and len(value) > 1:
        payloads.append(value[1:])
    for payload in payloads:
        raw = _b64dec(payload)
        if raw is None:
            continue
        parsed = _parse_pyrogram_packed(raw)
        if parsed:
            return parsed
    return None


def _extract_from_hex(raw: bytes) -> list[tuple[Optional[int], bytes]]:
    """Every plausible (dc_hint, auth_key) from a hex blob. dc_hint may be None."""
    out: list[tuple[Optional[int], bytes]] = []
    seen: set[tuple[Optional[int], bytes]] = set()

    def add(dc: Optional[int], key: bytes) -> None:
        if len(key) != 256:
            return
        if dc is not None and dc not in DC_INFO:
            dc = None
        marker = (dc, key)
        if marker not in seen:
            seen.add(marker)
            out.append(marker)

    packed = _parse_telethon_packed(raw)
    if packed:
        add(*packed)

    pyro = _parse_pyrogram_packed(raw)
    if pyro:
        add(*pyro)

    n = len(raw)
    if n == 256:
        add(None, raw)
    elif n == 257:
        add(raw[0], raw[1:])
    elif n == 258:
        for endian in ("little", "big"):
            add(int.from_bytes(raw[:2], endian), raw[2:])
        add(raw[0], raw[2:])  # pyrogram old: dc + test + key
    elif n == 260:
        for endian in ("little", "big"):
            add(int.from_bytes(raw[:4], endian), raw[4:])
    elif n == 261:
        add(int.from_bytes(raw[:4], "little"), raw[5:])
    elif n > 256:
        # last-resort: take the last 256 bytes as the key
        add(None, raw[-256:])
        add(raw[0], raw[1:257])

    return out


# ---------------------------------------------------------------------------
# Live sequential probe  (DC5 → 4 → 3 → 2 → 1)
# ---------------------------------------------------------------------------

async def _verify_session_string(session_string: str, label: str) -> Optional[str]:
    """Connect with an already-built Telethon string. Disconnect before return."""
    if not session_string or session_string[0] != "1":
        return None

    client = None
    try:
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=RPC_TIMEOUT)
        if not authorized:
            log.info("%s connected but not authorized", label)
            return None
        me = await asyncio.wait_for(client.get_me(), timeout=RPC_TIMEOUT)
        if me is None:
            log.info("%s get_me() returned None", label)
            return None
        saved = client.session.save()
        log.info("%s OK — user %s", label, me.id)
        return saved or session_string
    except asyncio.TimeoutError:
        log.warning("%s timed out", label)
    except AuthKeyUnregisteredError:
        log.warning("%s AUTH_KEY_UNREGISTERED", label)
    except AuthKeyDuplicatedError:
        log.warning("%s AUTH_KEY_DUPLICATED — this key is now dead", label)
    except Exception as exc:
        log.warning("%s failed: %s: %s", label, type(exc).__name__, exc)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
    return None


async def _probe_key(auth_key: bytes, dc_hint: Optional[int] = None) -> str:
    if len(auth_key) != 256:
        raise ValueError(f"Invalid auth_key length: {len(auth_key)}")

    order: list[int] = []
    if dc_hint in DC_INFO:
        order.append(dc_hint)
    for dc in DC_ORDER:
        if dc not in order:
            order.append(dc)

    tried = []
    for dc in order:
        label = f"DC{dc}"
        log.info("Trying %s ...", label)
        try:
            session_string = make_string(dc, auth_key)
        except Exception as exc:
            log.warning("%s build failed: %s", label, exc)
            tried.append(f"{label}: build failed")
            continue

        saved = await _verify_session_string(session_string, label)
        if saved:
            return saved
        tried.append(label)

    raise ValueError(
        "Auth key rejected on " + " → ".join(tried) + ". "
        "The key is dead, Telegram is unreachable, or API_ID/API_HASH is wrong."
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

async def hex_to_session_string(raw_input: str) -> str:
    raw_input = _clean(raw_input)
    if not raw_input:
        raise ValueError("Empty session input")

    if raw_input in _cache:
        return _cache[raw_input]

    # --- already a Telethon / Pyrogram string --------------------------------
    if not _is_hex(raw_input):
        parsed = _parse_telethon_string(raw_input) or _parse_pyrogram_string(raw_input)
        if not parsed:
            raise ValueError(
                "Input is not hex and not a Telethon/Pyrogram StringSession."
            )
        dc, key = parsed
        saved = await _probe_key(key, dc)
        _cache[raw_input] = saved
        return saved

    # --- hex -----------------------------------------------------------------
    raw = bytes.fromhex(raw_input)

    # hex(UTF-8 session string)
    try:
        decoded = raw.decode("ascii")
        parsed = _parse_telethon_string(decoded) or _parse_pyrogram_string(decoded)
        if parsed:
            dc, key = parsed
            saved = await _probe_key(key, dc)
            _cache[raw_input] = saved
            return saved
    except UnicodeDecodeError:
        pass

    candidates = _extract_from_hex(raw)
    if not candidates:
        raise ValueError(
            f"Unsupported hex size: {len(raw)} bytes ({len(raw) * 2} hex chars)."
        )

    last_error: Optional[Exception] = None
    seen_keys: set[bytes] = set()
    for dc_hint, auth_key in candidates:
        if auth_key in seen_keys and dc_hint is None:
            continue
        seen_keys.add(auth_key)
        try:
            saved = await _probe_key(auth_key, dc_hint)
            _cache[raw_input] = saved
            return saved
        except Exception as exc:
            last_error = exc
            log.warning("candidate failed: %s", exc)

    raise last_error or ValueError("Unable to verify hexadecimal session")
