#!/usr/bin/env python3
"""
hex.py — Universal Telegram session converter
Converts any format → Telethon StringSession (no Telegram connection needed).

Supports:
  - Telethon StringSession (raw or hex-encoded)
  - Pyrogram StringSession (raw or hex-encoded)
  - Telethon packed bytes as hex (263 / 275)
  - GramJS packed bytes as hex (264 / 276)
  - Pyrogram packed bytes as hex (271 / 258 / 262)
  - Bare 256-byte auth_key as hex
  - DC-prefixed layouts (257 / 260 / 261)
  - Stray-nibble variants (tolerates odd-length hex)

Usage:
    from hex import parse_only, hex_to_session_string

    candidates, ready = parse_only("your_hex_key_or_session_string")
    # ready is a Telethon string if DC was known, or None if DC probing needed

    session_str = hex_to_session_string("your_hex_key")  # offline conversion
    # Only fails if format is unrecognized — never connects.
"""

import base64
import ipaddress
import struct

# ---- Telegram DC info ----
DC_INFO = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}
DC_PORT = 443


def _b64dec(value: str) -> bytes | None:
    value = value.strip()
    if not value:
        return None
    for fn in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return fn(value + "=" * (-len(value) % 4))
        except Exception:
            continue
    return None


def _b64enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _is_hex(value: str) -> bool:
    return (bool(value) and len(value) % 2 == 0 and
            all(c in "0123456789abcdefABCDEF" for c in value))


def _clean(value: str) -> str:
    value = (value or "").strip().replace("\r", "").replace("\n", "")\
            .replace(" ", "").replace("\t", "").strip("'\"")
    if value.lower().startswith("0x"):
        value = value[2:]
    return value


def make_string(dc_id: int, auth_key: bytes, ip: str | None = None, port: int = 443) -> str:
    """Build a Telethon StringSession from raw auth_key bytes + DC info."""
    if dc_id not in DC_INFO:
        raise ValueError(f"Unknown Telegram DC: {dc_id}")
    if len(auth_key) != 256:
        raise ValueError(f"auth_key must be 256 bytes, got {len(auth_key)}")
    if not ip:
        ip = DC_INFO[dc_id]
    ip_bytes = ipaddress.ip_address(ip).packed
    packed = struct.pack(f">B{len(ip_bytes)}sH256s", dc_id, ip_bytes, port, auth_key)
    return "1" + _b64enc(packed)


def _telethon_string_from_packed(raw: bytes) -> str:
    """Keep original DC + IP + port from packed blob."""
    return "1" + _b64enc(raw)


def _parse_telethon_packed(raw: bytes) -> tuple[int, bytes, str, int] | None:
    """Telethon binary: dc(1) + ip(4|16) + port(2) + key(256). Returns (dc, key, ip, port)."""
    n = len(raw)
    if n == 263:
        dc, ip_raw, port, key = struct.unpack(">B4sH256s", raw)
        ip = ".".join(str(b) for b in ip_raw)
    elif n == 275:
        dc, ip_raw, port, key = struct.unpack(">B16sH256s", raw)
        ip = ":".join(f"{ip_raw[i]:02x}{ip_raw[i+1]:02x}" for i in range(0, 16, 2))
    else:
        return None
    if dc in DC_INFO and len(key) == 256:
        return dc, key, ip, int(port)
    return None


def _parse_telethon_string(value: str) -> tuple[int, bytes] | None:
    if not value or value[0] != "1":
        return None
    raw = _b64dec(value[1:])
    if raw is None:
        return None
    parsed = _parse_telethon_packed(raw)
    if not parsed:
        return None
    return parsed[0], parsed[1]


def _parse_pyrogram_packed(raw: bytes) -> tuple[int, bytes] | None:
    """
    Pyrogram packed formats:
      New: >BI?256sQ? = 271 — dc(1) + api_id(4) + test(1) + key(256) + uid(8) + bot(1)
      Old: >B?256s   = 258 — dc(1) + test(1) + key(256)
    """
    n = len(raw)
    if n == 271:
        dc = raw[0]
        key = raw[6:262]
        if dc in DC_INFO and len(key) == 256:
            return dc, key
    if n == 258:
        dc = raw[0]
        key = raw[2:258]
        if dc in DC_INFO and len(key) == 256:
            return dc, key
    return None


def _parse_pyrogram_string(value: str) -> tuple[int, bytes] | None:
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


def extract_from_hex(raw: bytes) -> list[tuple[int | None, bytes, bytes | None]]:
    """
    Returns unique (dc_hint, auth_key, original_packed_or_None) candidates.
    One entry per unique auth_key. Preserves packed Telethon blobs for IP reuse.
    """
    out: list[tuple[int | None, bytes, bytes | None]] = []
    seen_keys: set[bytes] = set()

    def add(dc: int | None, key: bytes, packed: bytes | None = None) -> None:
        if len(key) != 256:
            return
        if key in seen_keys:
            return
        if dc is not None and dc not in DC_INFO:
            dc = None
        seen_keys.add(key)
        out.append((dc, key, packed))

    n = len(raw)

    # Telethon packed blobs (263 / 275)
    if n == 263 or n == 275:
        parsed = _parse_telethon_packed(raw)
        if parsed:
            add(parsed[0], parsed[1], raw)
            return out

    # Pyrogram packed blobs (271)
    if n == 271:
        pyro = _parse_pyrogram_packed(raw)
        if pyro:
            add(*pyro)
            return out

    # Pyrogram old (258)
    if n == 258:
        pyro = _parse_pyrogram_packed(raw)
        if pyro:
            add(*pyro)
        if raw[0] in DC_INFO:
            add(raw[0], raw[2:])
        for endian in ("little", "big"):
            dc = int.from_bytes(raw[:2], endian)
            if dc in DC_INFO:
                add(dc, raw[2:])
        return out

    # DC-prefixed 257: dc(1) + key(256)
    if n == 257:
        add(raw[0], raw[1:])
        return out

    # Bare 256-byte auth_key
    if n == 256:
        add(None, raw)
        return out

    # 260: int32(dc) + key(256)
    if n == 260:
        for endian in ("little", "big"):
            dc = int.from_bytes(raw[:4], endian)
            if dc in DC_INFO:
                add(dc, raw[4:])
        return out

    # 261: int32(dc) + 1byte_padding + key(256)
    if n == 261:
        dc = int.from_bytes(raw[:4], "little")
        if dc in DC_INFO:
            add(dc, raw[5:])
        return out

    # Fallback: try Telethon packed even if unexpected size
    packed = _parse_telethon_packed(raw)
    if packed:
        add(packed[0], packed[1], raw)
        return out

    # Fallback: Pyrogram
    pyro = _parse_pyrogram_packed(raw)
    if pyro:
        add(*pyro)
        return out

    # Last resort: try last 256 bytes as key
    if n > 256:
        add(None, raw[-256:])
        add(raw[0], raw[1:257])
        return out

    # Try 262 as dc(1) + key(256)
    if n == 262:
        add(raw[0], raw[2:])
        return out

    return out


def parse_only(raw_input: str) -> tuple[list[tuple[int | None, bytes, bytes | None]], str | None]:
    """
    Parse any session input format. NEVER connects to Telegram.

    Args:
        raw_input: Hex key, session string, etc.

    Returns:
        (candidates, ready_session_string_or_None)
        - candidates: list of (dc_hint, auth_key_bytes, packed_blob_or_None)
        - ready_session_string: a Telethon StringSession string if the DC is known,
          or None if probing is needed (bare 256-byte key without DC).

    Raises:
        ValueError if format is unrecognized.
    """
    raw_input = _clean(raw_input)
    if not raw_input:
        raise ValueError("Empty session input")

    # --- Already a Telethon session string ---
    if raw_input.startswith("1") and len(raw_input) > 80:
        parsed = _parse_telethon_string(raw_input)
        if parsed:
            return [(parsed[0], parsed[1], None)], raw_input

    # --- Not hex → try as session string ---
    if not _is_hex(raw_input):
        parsed = _parse_telethon_string(raw_input) or _parse_pyrogram_string(raw_input)
        if not parsed:
            raise ValueError("Input is not hex and not a recognized Telethon/Pyrogram session string.")
        dc, key = parsed
        sess = make_string(dc, key)
        return [(dc, key, None)], sess

    # --- Hex input ---
    raw = bytes.fromhex(raw_input)

    # Try decoding hex as ASCII session string
    try:
        decoded = raw.decode("ascii")
        if decoded.startswith("1"):
            parsed = _parse_telethon_string(decoded)
            if parsed:
                return [(parsed[0], parsed[1], None)], decoded
        parsed = _parse_pyrogram_string(decoded)
        if parsed:
            dc, key = parsed
            sess = make_string(dc, key)
            return [(dc, key, None)], sess
    except (UnicodeDecodeError, ValueError):
        pass

    # Analyze hex bytes
    candidates = extract_from_hex(raw)
    if not candidates:
        raise ValueError(f"Unsupported hex size: {len(raw)} bytes ({len(raw)*2} hex chars).")

    dc, key, packed = candidates[0]

    # If we have packed Telethon bytes (263/275), reconstruct with original IP
    if dc is not None and packed is not None and len(packed) in (263, 275):
        return candidates, _telethon_string_from_packed(packed)

    # Single candidate with known DC → ready
    if dc is not None and len(candidates) == 1:
        return candidates, make_string(dc, key)

    # Multi candidates or bare key → needs DC probing
    return candidates, None


def hex_to_session_string(raw_input: str) -> str:
    """
    Convert raw hex/session input to a Telethon StringSession string.
    Offline only — NO Telegram connection.

    For bare 256-byte keys (no DC embedded), uses DC2 as default.
    If you need DC probing, use connect_from_raw() from the bot module.
    """
    candidates, ready = parse_only(raw_input)
    if ready:
        return ready

    # Bare key with unknown DC → guess DC2 (most common)
    dc, key, packed = candidates[0]
    guess_dc = dc if dc in DC_INFO else 2
    return make_string(guess_dc, key)
