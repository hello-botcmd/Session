"""Convert a panel hex auth_key into a real Telethon StringSession.

A raw 256-byte auth_key carries NO dc_id - it is 256 random bytes and its DC
binding exists only on Telegram's side. "Detecting the DC" therefore means
probing the key against DCs 1-5 and letting the server answer
AUTH_KEY_UNREGISTERED (wrong DC / dead key) or accept the init request.

That probe is a real network round-trip, so the host MUST be able to reach
Telegram. If Telegram is blocked on the host, set PROXY below - otherwise
every probe fails at TCP level and even a valid key reports "not authorized".
"""

from __future__ import annotations

import asyncio
import logging

try:
    from telethon import TelegramClient, __version__ as _TL_VER
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession
except ImportError as exc:  # Telethon v2 removed StringSession entirely
    raise ImportError(
        "Telethon is missing or is v2.x. Install: "
        "pip install 'telethon>=1.28,<2'"
    ) from exc

from config import API_HASH, API_ID

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Network config
# ---------------------------------------------------------------------------
# Required if the verifier's host cannot reach Telegram directly.
#   PROXY = {"hostname": "127.0.0.1", "port": 1080,
#            "username": "...", "password": "..."}
# Requires: pip install python-socks[asyncio]
PROXY: dict | None = None

USE_IPV6 = False          # try IPv6 DC addresses when IPv4 is blocked/throttled
PROBE_TIMEOUT = 15        # seconds per DC attempt

DC_IPV4 = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}
DC_IPV6 = {
    1: "2001:b28:f23d:f001::a",
    2: "2001:67c:4e8:f002::a",
    3: "2001:b28:f23d:f003::a",
    4: "2001:67c:4e8:f004::a",
    5: "2001:b28:f23f:f005::a",
}

_cache: dict[str, str] = {}


def _is_hex(s: str) -> bool:
    s = s.strip()
    return bool(s) and len(s) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in s)


def _bad_hex_reason(s: str) -> str:
    """Explain why a hex-looking token is invalid."""
    s = s.strip()
    if not s:
        return "empty input"
    if len(s) % 2 == 1:
        return (
            f"hex length is ODD ({len(s)} chars = {len(s) // 2} bytes + 1 nibble). "
            "A digit is missing or the token was truncated."
        )
    for i, c in enumerate(s):
        if c not in "0123456789abcdefABCDEF":
            return f"non-hex character {c!r} at position {i}"
    return f"valid hex, {len(s) // 2} bytes"


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
    address = DC_IPV6[dc_id] if USE_IPV6 else DC_IPV4[dc_id]
    session = StringSession()
    session.set_dc(dc_id, address, 443)
    session.auth_key = AuthKey(auth_key)
    saved = session.save()
    if not saved:
        raise ValueError("Telethon refused to save the session (empty auth_key?)")
    # Round-trip sanity: the string must decode back to the same key
    check = StringSession(saved)
    if check.auth_key is None or check.auth_key.key != auth_key:
        raise ValueError("round-trip mismatch while building session string")
    return saved


def _extract_keys(raw: bytes) -> list[tuple[int | None, bytes]]:
    """Return (dc_hint, auth_key) candidates from raw panel bytes."""
    out: list[tuple[int | None, bytes]] = []

    if len(raw) == 256:            # A) bare auth_key -- no DC info at all
        out.append((None, raw))

    elif len(raw) == 257:          # B) 1-byte dc + 256-byte key
        dc = raw[0]
        out.append((dc if dc in DC_IPV4 else None, raw[1:]))

    elif len(raw) == 258:          # E) 2-byte dc + 256-byte key
        for endian in ("big", "little"):
            dc = int.from_bytes(raw[:2], endian)
            if dc in DC_IPV4:
                out.append((dc, raw[2:]))
        if not out:
            out.append((None, raw[2:]))

    elif len(raw) == 260:          # C) 4-byte dc + 256-byte key
        for endian in ("big", "little"):
            dc = int.from_bytes(raw[:4], endian)
            if dc in DC_IPV4:
                out.append((dc, raw[4:]))
        if not out:
            out.append((None, raw[4:]))

    elif len(raw) == 261:          # D) Pyrogram raw: dc LE + test_mode + key
        dc = int.from_bytes(raw[:4], "little")
        out.append((dc if dc in DC_IPV4 else None, raw[5:]))

    elif len(raw) == 264:          # F) auth_key_id(8 LE) + 256-byte key
        out.append((None, raw[8:]))

    else:
        raise ValueError(
            f"Unsupported hex length {len(raw)} bytes "
            f"({len(raw) * 2} hex chars). Expected 256/257/258/260/261/264."
        )

    return out


async def _authorized(session_str: str) -> tuple[str | None, str]:
    """Connect with a candidate string. Returns (saved_string, reason)."""
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH, proxy=PROXY)
    try:
        await asyncio.wait_for(client.connect(), timeout=PROBE_TIMEOUT)
        if not await client.is_user_authorized():
            return None, "server rejected the auth key (AUTH_KEY_UNREGISTERED)"

        # Stronger check: confirm a real user is actually bound to the key.
        try:
            me = await asyncio.wait_for(client.get_me(), timeout=PROBE_TIMEOUT)
        except telethon.errors.AuthKeyUnregisteredError:
            return None, "AUTH_KEY_UNREGISTERED (wrong DC or dead key)"
        if me is None:
            return None, "auth key registered but no user bound (useless session)"

        saved = client.session.save() or session_str
        return saved, f"authorized (user {me.id})"
    except asyncio.TimeoutError:
        return None, "connect timed out (no route to Telegram - proxy needed?)"
    except telethon.errors.AuthKeyUnregisteredError:
        return None, "AUTH_KEY_UNREGISTERED (wrong DC or dead key)"
    except telethon.errors.ApiIdInvalidError:
        return None, f"API_ID={API_ID} / API_HASH invalid or banned"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _probe(auth_key: bytes, dc_hint: int | None = None) -> str:
    """Try the hinted DC first, then the rest. Collect a reason for every DC."""
    order = list(DC_IPV4)
    if dc_hint in DC_IPV4:
        order.remove(dc_hint)
        order.insert(0, dc_hint)

    reasons: list[str] = []
    for dc in order:
        try:
            candidate = make_string(dc, auth_key)
        except Exception as exc:
            reasons.append(f"DC{dc}: encode failed ({exc})")
            continue
        saved, reason = await _authorized(candidate)
        if saved:
            log.info("auth_key authorized on DC%d (%s)", dc, reason)
            return saved
        reasons.append(f"DC{dc}: {reason}")

    raise ValueError(
        "Could not authorize this hex on any Telegram DC (1-5).\n"
        "Per-DC results:\n  " + "\n  ".join(reasons) + "\n"
        "Note: a bare 256-byte key has no DC info, so all 5 DCs must be probed.\n"
        "If every DC fails with timeouts/connection errors, the host cannot reach\n"
        "Telegram directly - set PROXY (or run the verifier on a host with access)."
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
        raw = bytes.fromhex(raw_input)
        try:
            as_text = raw.decode("ascii", errors="strict")
            if _looks_like_telethon(as_text):
                _cache[raw_input] = as_text
                return as_text
        except (ValueError, UnicodeDecodeError):
            pass

        key_candidates = _extract_keys(raw)
        last_err: Exception | None = None
        for dc_hint, key in key_candidates:
            try:
                saved = await _probe(key, dc_hint)
                _cache[raw_input] = saved
                return saved
            except Exception as exc:
                last_err = exc
        raise last_err or ValueError("Unsupported hex format")

    # Hex-looking but invalid -> say exactly why (e.g. odd length)
    stripped = raw_input.strip()
    if all(c in "0123456789abcdefABCDEF" for c in stripped):
        raise ValueError(_bad_hex_reason(stripped))

    # Last chance: maybe it's a Telethon string that decode() is picky about
    try:
        StringSession(raw_input)
        _cache[raw_input] = raw_input
        return raw_input
    except Exception:
        raise ValueError(
            "Input is neither a Telethon StringSession nor a hex auth_key"
        ) from None
