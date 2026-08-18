    """
Convert any supported Telegram session input into a *verified* Telethon
StringSession.

Supported inputs:
  1. Telethon StringSession          (raw or hex-encoded)
  2. Pyrogram StringSession          (raw or hex-encoded)
  3. Bare 256-byte auth key          (hex)
  4. DC + auth_key panel layouts     (hex, dc as 1/2/4 bytes, optional test flag)

A candidate is only accepted after Telegram confirms it (get_me() succeeds).
"""

from __future__ import annotations

import asyncio
import base64
import logging
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

CONNECT_TIMEOUT = 12
RPC_TIMEOUT = 10

_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_hex(value: str) -> bool:
    value = value.strip()
    return (
        bool(value)
        and len(value) % 2 == 0
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


def _b64dec(value: str) -> Optional[bytes]:
    value = value.strip()
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
    if not result:
        raise ValueError("Telethon failed to serialize the session")
    return result


# ---------------------------------------------------------------------------
# Session-string parsing
# ---------------------------------------------------------------------------

def _parse_telethon_string(value: str) -> Optional[StringSession]:
    """StringSession() is lenient — also require a real DC + 256-byte key."""
    try:
        ss = StringSession(value)
    except Exception:
        return None
    dc_id = getattr(ss, "dc_id", 0)
    key = getattr(ss, "auth_key", None)
    if dc_id not in DC_INFO:
        return None
    if key is None or len(key.key) != 256:
        return None
    return ss


def _session_candidates(value: str) -> list[str]:
    """Generate every plausible Telethon session string from the input."""
    out: list[str] = []
    seen: set[str] = set()

    def add(ss: str) -> None:
        if ss and ss not in seen:
            seen.add(ss)
            out.append(ss)

    tss = _parse_telethon_string(value)
    if tss is not None:
        add(tss.save())

    # Pyrogram: "1" + b64url(dc(1) + test(1) + auth_key(256))
    payloads = [value]
    if value.startswith("1"):
        payloads.append(value[1:])

    for payload in payloads:
        raw = _b64dec(payload)
        if raw is None or len(raw) < 256:
            continue

        # dc + test + key  (258 bytes)
        if len(raw) >= 258:
            dc = raw[0]
            key = raw[2:258]
            if dc in DC_INFO and len(key) == 256:
                try:
                    add(make_string(dc, key))
                except Exception:
                    pass

        # dc + key  (257 bytes, no test flag)
        if len(raw) >= 257:
            dc = raw[0]
            key = raw[1:257]
            if dc in DC_INFO and len(key) == 256:
                try:
                    add(make_string(dc, key))
                except Exception:
                    pass

        # bare key
        if len(raw) == 256:
            for dc in DC_INFO:
                try:
                    add(make_string(dc, raw))
                except Exception:
                    pass

    return out


def _extract_candidates(raw: bytes) -> list[tuple[Optional[int], bytes]]:
    """Extract (dc_hint, auth_key) pairs from hex panel layouts."""
    candidates: list[tuple[Optional[int], bytes]] = []

    if len(raw) == 256:
        candidates.append((None, raw))
    elif len(raw) == 257:
        dc = raw[0]
        candidates.append((dc if dc in DC_INFO else None, raw[1:]))
    elif len(raw) == 258:
        for endian in ("little", "big"):
            dc = int.from_bytes(raw[:2], endian)
            if dc in DC_INFO:
                candidates.append((dc, raw[2:]))
        if not any(c[0] is not None for c in candidates):
            candidates.append((None, raw[2:]))
    elif len(raw) == 260:
        for endian in ("little", "big"):
            dc = int.from_bytes(raw[:4], endian)
            if dc in DC_INFO:
                candidates.append((dc, raw[4:]))
        if not any(c[0] is not None for c in candidates):
            candidates.append((None, raw[4:]))
    elif len(raw) == 261:
        dc = int.from_bytes(raw[:4], "little")
        candidates.append((dc if dc in DC_INFO else None, raw[5:]))
    else:
        raise ValueError(
            f"Unsupported hex size: {len(raw)} bytes "
            f"({len(raw) * 2} hex chars). Expected 256/257/258/260/261."
        )

    unique, seen = [], set()
    for dc, key in candidates:
        if len(key) != 256:
            continue
        marker = (dc, key)
        if marker not in seen:
            seen.add(marker)
            unique.append((dc, key))
    return unique


# ---------------------------------------------------------------------------
# Live DC verification
# ---------------------------------------------------------------------------

async def _try_session(session_string: str, label: str) -> Optional[str]:
    """Connect + get_me(). Returns the (possibly migrated) session string."""
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
        authorized = await asyncio.wait_for(
            client.is_user_authorized(), timeout=RPC_TIMEOUT
        )
        if not authorized:
            log.info("%s connected but not authorized", label)
            return None
        me = await asyncio.wait_for(client.get_me(), timeout=RPC_TIMEOUT)
        if me is None:
            log.info("%s authorized flag set but get_me() is None", label)
            return None
        saved = client.session.save()
        log.info("%s OK — user %s", label, me.id)
        return saved or session_string
    except asyncio.TimeoutError:
        log.warning("%s timed out", label)
    except (AuthKeyUnregisteredError, AuthKeyDuplicatedError) as exc:
        log.warning("%s auth-key rejected: %s", label, type(exc).__name__)
    except Exception as exc:
        log.warning("%s failed: %s: %s", label, type(exc).__name__, exc)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return None


async def _probe_sessions(sessions: list[str]) -> str:
    """Try all session strings in parallel. First success wins."""
    if not sessions:
        raise ValueError("No session candidates to probe")

    tasks = [
        asyncio.create_task(_try_session(ss, f"cand#{i}"))
        for i, ss in enumerate(sessions)
    ]
    try:
        for fut in asyncio.as_completed(tasks):
            result = await fut
            if result:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return result
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    raise ValueError(
        "Auth key could not be verified on any DC. "
        "The session is dead, the DC is unreachable, or the format is unknown."
    )


async def _probe_auth_key(auth_key: bytes, dc_hint: Optional[int] = None) -> str:
    if len(auth_key) != 256:
        raise ValueError(f"Invalid auth_key length: {len(auth_key)}")

    order = [dc_hint] if dc_hint in DC_INFO else list(DC_INFO)
    sessions: list[str] = []
    for dc in order:
        try:
            sessions.append(make_string(dc, auth_key))
        except Exception as exc:
            log.warning("DC%d build failed: %s", dc, exc)

    # If a hint failed, still try the other DCs.
    if dc_hint in DC_INFO:
        for dc in DC_INFO:
            if dc == dc_hint:
                continue
            try:
                sessions.append(make_string(dc, auth_key))
            except Exception:
                pass

    return await _probe_sessions(sessions)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def hex_to_session_string(raw_input: str) -> str:
    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise ValueError("Empty session input")

    if raw_input in _cache:
        return _cache[raw_input]

    # 1. Already a valid Telethon / Pyrogram session string
    if not _is_hex(raw_input):
        sessions = _session_candidates(raw_input)
        if sessions:
            saved = await _probe_sessions(sessions)
            _cache[raw_input] = saved
            return saved
        raise ValueError(
            "Input is neither a valid Telethon/Pyrogram StringSession "
            "nor a supported hexadecimal auth-key format."
        )

    # 2. Hex input
    raw = bytes.fromhex(raw_input)

    # 2a. Hex-encoded UTF-8 session string
    try:
        decoded = raw.decode("ascii")
        sessions = _session_candidates(decoded)
        if sessions:
            saved = await _probe_sessions(sessions)
            _cache[raw_input] = saved
            return saved
    except UnicodeDecodeError:
        pass

    # 2b. Panel auth-key layouts
    last_error: Optional[Exception] = None
    for dc_hint, auth_key in _extract_candidates(raw):
        try:
            saved = await _probe_auth_key(auth_key, dc_hint)
            _cache[raw_input] = saved
            return saved
        except Exception as exc:
            last_error = exc
            log.warning("Candidate failed: %s", exc)

    raise last_error or ValueError("Unable to verify hexadecimal session")
