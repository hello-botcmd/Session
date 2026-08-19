"""
Convert Telegram session input into a Telethon StringSession.

Supported:
  - Telethon StringSession (raw or hex-encoded)
  - Pyrogram StringSession (raw or hex-encoded)
  - Telethon packed bytes as hex (263 / 275)
  - Pyrogram packed bytes as hex (271 / 258)
  - Bare 256-byte auth_key as hex
  - DC-prefixed panel layouts (257 / 258 / 260 / 261)

IMPORTANT:
  This module does NOT connect to Telegram just to convert.
  Live connect happens once, in connect_from_raw().
  Connecting twice with the same auth_key = AUTH_KEY_DUPLICATED = dead key.
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

# Hint first, then most common production DCs.
DC_ORDER = (2, 4, 5, 1, 3)

CONNECT_TIMEOUT = 12
RPC_TIMEOUT = 10

_cache: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def _clean(value: str) -> str:
    value = (
        (value or "")
        .strip()
        .replace("\r", "")
        .replace("\n", "")
        .replace(" ", "")
        .replace("\t", "")
        .strip("'\"")
    )
    if value.lower().startswith("0x"):
        value = value[2:]
    return value


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


def make_string(dc_id: int, auth_key: bytes, ip: str | None = None, port: int = 443) -> str:
    if dc_id not in DC_INFO:
        raise ValueError(f"Unknown Telegram DC: {dc_id}")
    if len(auth_key) != 256:
        raise ValueError(f"auth_key must be exactly 256 bytes, got {len(auth_key)}")
    if not ip:
        ip, port = DC_INFO[dc_id]
    session = StringSession()
    session.set_dc(dc_id, ip, port)
    session.auth_key = AuthKey(auth_key)
    result = session.save()
    if not result or result[0] != "1":
        raise ValueError("Telethon failed to serialize the session")
    return result


def _telethon_string_from_packed(raw: bytes) -> str:
    """Keep original DC + IP + port. Do not rebuild with hardcoded IPs."""
    return "1" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _client(session_string: str) -> TelegramClient:
    # auto_reconnect MUST stay False. A background reconnect during
    # another attempt is AUTH_KEY_DUPLICATED and Telegram revokes the key.
    return TelegramClient(
        StringSession(session_string),
        API_ID,
        API_HASH,
        connection_retries=0,
        auto_reconnect=False,
        request_retries=1,
        receive_updates=False,
    )


async def _lock_for(key: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


async def _safe_disconnect(client: TelegramClient | None) -> None:
    if client is None:
        return
    try:
        await client.disconnect()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Parsers — never call StringSession() on untrusted input
# ---------------------------------------------------------------------------

def _parse_telethon_packed(raw: bytes) -> Optional[tuple[int, bytes, str, int]]:
    """Telethon binary: dc(1) + ip(4|16) + port(2) + key(256)."""
    ip: str
    if len(raw) == 263:
        dc, ip_raw, port, key = struct.unpack(">B4sH256s", raw)
        ip = ".".join(str(b) for b in ip_raw)
    elif len(raw) == 275:
        dc, ip_raw, port, key = struct.unpack(">B16sH256s", raw)
        ip = ":".join(f"{ip_raw[i]:02x}{ip_raw[i + 1]:02x}" for i in range(0, 16, 2))
    else:
        return None
    if dc in DC_INFO and len(key) == 256:
        return dc, key, ip, int(port)
    return None


def _parse_telethon_string(value: str) -> Optional[tuple[int, bytes]]:
    if not value or value[0] != "1":
        return None
    raw = _b64dec(value[1:])
    if raw is None:
        return None
    parsed = _parse_telethon_packed(raw)
    if not parsed:
        return None
    return parsed[0], parsed[1]


def _parse_pyrogram_packed(raw: bytes) -> Optional[tuple[int, bytes]]:
    """
    Exact sizes only. `>=` was matching Telethon 263/275 and extracting a wrong key.
    New:  >BI?256sQ?   = dc + api_id + test + key + user_id + is_bot  (271)
    Old:  dc + test + key                                             (258)
    """
    if len(raw) == 271:
        dc = raw[0]
        key = raw[6:262]
        if dc in DC_INFO and len(key) == 256:
            return dc, key
    if len(raw) == 258:
        dc = raw[0]
        key = raw[2:258]
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


def _extract_from_hex(raw: bytes) -> list[tuple[Optional[int], bytes, Optional[bytes]]]:
    """
    Unique (dc_hint, auth_key, original_packed_or_None) candidates.
    One entry per auth_key. Packed Telethon bytes are kept so we reuse the real IP.
    """
    out: list[tuple[Optional[int], bytes, Optional[bytes]]] = []
    seen_keys: set[bytes] = set()

    def add(dc: Optional[int], key: bytes, packed: Optional[bytes] = None) -> None:
        if len(key) != 256:
            return
        if key in seen_keys:
            return
        if dc is not None and dc not in DC_INFO:
            dc = None
        seen_keys.add(key)
        out.append((dc, key, packed))

    n = len(raw)

    if n == 263 or n == 275:
        packed = _parse_telethon_packed(raw)
        if packed:
            add(packed[0], packed[1], raw)
            return out

    if n == 271:
        pyro = _parse_pyrogram_packed(raw)
        if pyro:
            add(*pyro)
            return out

    if n == 258:
        pyro = _parse_pyrogram_packed(raw)
        if pyro:
            add(*pyro)
        if raw[0] in DC_INFO:
            add(raw[0], raw[2:])
        for endian in ("little", "big"):
            add(int.from_bytes(raw[:2], endian), raw[2:])
        return out

    if n == 257:
        add(raw[0], raw[1:])
        return out

    if n == 256:
        add(None, raw)
        return out

    if n == 260:
        for endian in ("little", "big"):
            add(int.from_bytes(raw[:4], endian), raw[4:])
        return out

    if n == 261:
        add(int.from_bytes(raw[:4], "little"), raw[5:])
        return out

    packed = _parse_telethon_packed(raw)
    if packed:
        add(packed[0], packed[1], raw)
        return out

    pyro = _parse_pyrogram_packed(raw)
    if pyro:
        add(*pyro)
        return out

    if n > 256:
        add(None, raw[-256:])
        add(raw[0], raw[1:257])

    return out

def _build_string(dc: int, key: bytes, packed: Optional[bytes]) -> str:
    if packed is not None and len(packed) in (263, 275):
        return _telethon_string_from_packed(packed)
    return make_string(dc, key)


def parse_only(raw_input: str) -> tuple[list[tuple[Optional[int], bytes, Optional[bytes]]], Optional[str]]:
    """
    Returns (candidates, ready_session_string_or_None).
    ready_session_string is set when no DC probe is required.
    Never talks to Telegram.
    """
    raw_input = _clean(raw_input)
    if not raw_input:
        raise ValueError("Empty session input")

    # Already a Telethon string — including hex-charset strings that start with 1.
    if raw_input.startswith("1") and len(raw_input) > 80:
        parsed = _parse_telethon_string(raw_input)
        if parsed:
            return [(parsed[0], parsed[1], None)], raw_input

    if not _is_hex(raw_input):
        parsed = _parse_telethon_string(raw_input) or _parse_pyrogram_string(raw_input)
        if not parsed:
            raise ValueError("Input is not hex and not a Telethon/Pyrogram StringSession.")
        dc, key = parsed
        return [(dc, key, None)], make_string(dc, key)

    raw = bytes.fromhex(raw_input)

    try:
        decoded = raw.decode("ascii")
        if decoded.startswith("1"):
            parsed = _parse_telethon_string(decoded)
            if parsed:
                return [(parsed[0], parsed[1], None)], decoded
        parsed = _parse_pyrogram_string(decoded)
        if parsed:
            dc, key = parsed
            return [(dc, key, None)], make_string(dc, key)
    except UnicodeDecodeError:
        pass

    candidates = _extract_from_hex(raw)
    if not candidates:
        raise ValueError(
            f"Unsupported hex size: {len(raw)} bytes ({len(raw) * 2} hex chars)."
        )

    dc, key, packed = candidates[0]
    if dc in DC_INFO and packed is not None and len(packed) in (263, 275):
        return candidates, _telethon_string_from_packed(packed)
    if dc in DC_INFO and len(candidates) == 1:
        return candidates, make_string(dc, key)
    return candidates, None


async def hex_to_session_string(raw_input: str) -> str:
    """
    Convert to a Telethon string. No Telegram connection.
    Bare keys with unknown DC are NOT cached here — connect_from_raw()
    is the only function allowed to probe DCs.
    """
    raw_input = _clean(raw_input)
    if not raw_input:
        raise ValueError("Empty session input")
    if raw_input in _cache:
        return _cache[raw_input]

    candidates, ready = parse_only(raw_input)
    if ready:
        _cache[raw_input] = ready
        return ready

    dc, key, packed = candidates[0]
    guess_dc = dc if dc in DC_INFO else 2
    # Placeholder only. Do not cache — wrong DC must not stick.
    return _build_string(guess_dc, key, packed)


# ---------------------------------------------------------------------------
# Single live connect
# ---------------------------------------------------------------------------

async def _try_string(session_string: str, label: str) -> Optional[TelegramClient]:
    client = None
    try:
        client = _client(session_string)
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
        authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=RPC_TIMEOUT)
        if not authorized:
            log.info("%s connected but not authorized", label)
            await _safe_disconnect(client)
            return None
        me = await asyncio.wait_for(client.get_me(), timeout=RPC_TIMEOUT)
        if me is None:
            log.info("%s get_me() returned None", label)
            await _safe_disconnect(client)
            return None
        log.info("%s OK — user %s", label, me.id)
        return client
    except asyncio.TimeoutError:
        log.warning("%s timed out", label)
    except AuthKeyUnregisteredError:
        log.warning("%s AUTH_KEY_UNREGISTERED (wrong DC or dead key)", label)
    except AuthKeyDuplicatedError:
        await _safe_disconnect(client)
        raise ValueError(
            f"{label}: AUTH_KEY_DUPLICATED — this key was used twice at once "
            "and Telegram has now revoked it."
        )
    except Exception as exc:
        log.warning("%s failed: %s: %s", label, type(exc).__name__, exc)
    await _safe_disconnect(client)
    return None


async def connect_from_raw(raw_input: str) -> TelegramClient:
    """
    Parse + ONE authorized connection. Caller owns the client and must disconnect.
    Never connect, disconnect, then connect again with the same key.
    """
    raw_input = _clean(raw_input)
    if not raw_input:
        raise ValueError("Empty session input")

    lock = await _lock_for(raw_input)
    async with lock:
        if raw_input in _cache:
            client = await _try_string(_cache[raw_input], "cached")
            if client is not None:
                return client
            _cache.pop(raw_input, None)

        candidates, ready = parse_only(raw_input)

        if ready:
            client = await _try_string(ready, f"DC-known")
            if client is not None:
                saved = client.session.save() or ready
                _cache[raw_input] = saved
                return client
            # Known-DC string failed — fall through and probe other DCs
            # with the same key, still one connection at a time.

        last_error: Optional[Exception] = None
        for dc_hint, auth_key, packed in candidates:
            order: list[int] = []
            if dc_hint in DC_INFO:
                order.append(dc_hint)
            for dc in DC_ORDER:
                if dc not in order:
                    order.append(dc)

            tried: list[str] = []
            for dc in order:
                label = f"DC{dc}"
                log.info("Trying %s ...", label)
                try:
                    session_string = _build_string(dc, auth_key, packed if dc == dc_hint else None)
                except Exception as exc:
                    tried.append(f"{label}: build failed")
                    last_error = exc
                    continue

                try:
                    client = await _try_string(session_string, label)
                except ValueError:
                    # AUTH_KEY_DUPLICATED — stop immediately, key is dead
                    raise

                if client is not None:
                    saved = client.session.save() or session_string
                    _cache[raw_input] = saved
                    return client

                tried.append(label)
                await asyncio.sleep(0.4)

            last_error = ValueError(
                "Auth key rejected on " + " → ".join(tried) + ". "
                "Wrong DC, dead key, Telegram unreachable, or API_ID/API_HASH is wrong."
            )

        raise last_error or ValueError("Unable to verify session")
