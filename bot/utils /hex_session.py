import asyncio
import base64

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_HASH, API_ID

# Telegram production data centers (port 443)
DC_MAP = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

_conv_cache = {}


def is_hex_string(s: str) -> bool:
    return all(c in "0123456789abcdefABCDEF" for c in s) and len(s) % 2 == 0


def build_v2_session(dc_id: int, server_address: str, port: int, auth_key: bytes) -> str:
    """Build a Telethon v2 StringSession string from raw components."""
    address = server_address.encode()
    data = bytearray()
    data.append(2)                       # version
    data += dc_id.to_bytes(4, "big")     # dc_id
    data.append(len(address))            # address length
    data += address                      # server address
    data += port.to_bytes(2, "big")      # port
    data += auth_key                     # 256-byte auth key
    return base64.urlsafe_b64encode(bytes(data)).decode("ascii")


async def _test_session(session_str: str) -> bool:
    """Connect with a candidate session string; True if the account authorizes."""
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    try:
        await asyncio.wait_for(client.connect(), timeout=8)
        return bool(await client.is_user_authorized())
    except Exception:
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def _candidates(raw: bytes) -> list:
    """Build candidate session strings from raw hex-decoded bytes."""
    cands = []

    # A) 256 bytes = bare auth key -> try every DC (your panel's format)
    if len(raw) == 256:
        for dc, ip in DC_MAP.items():
            cands.append(build_v2_session(dc, ip, 443, raw))

    # B) 260 bytes = dc_id (4) + auth_key (256)  -- some panels
    elif len(raw) == 260:
        for endian in ("big", "little"):
            dc = int.from_bytes(raw[:4], endian)
            if dc in DC_MAP:
                cands.append(build_v2_session(dc, DC_MAP[dc], 443, raw[4:]))

    # C) 261 bytes = Pyrogram raw (dc_id LE + test_mode + auth_key)
    elif len(raw) == 261:
        dc = int.from_bytes(raw[:4], "little")
        if dc in DC_MAP:
            cands.append(build_v2_session(dc, DC_MAP[dc], 443, raw[5:]))

    return cands


async def hex_to_session_string(hex_str: str) -> str:
    """Turn a panel hex (or a normal session string) into a working Telethon
    StringSession string. Results are cached in memory."""
    hex_str = hex_str.strip()
    if hex_str in _conv_cache:
        return _conv_cache[hex_str]

    # Already a valid Telethon session string? (normal base64 format)
    try:
        StringSession(hex_str)
        _conv_cache[hex_str] = hex_str
        return hex_str
    except ValueError:
        pass

    if not is_hex_string(hex_str):
        raise ValueError("Input is neither a Telethon session string nor hex")

    raw = bytes.fromhex(hex_str)

    # Probe all candidate DCs in parallel, keep the first that authorizes
    results = await asyncio.gather(*[_test_session(c) for c in _candidates(raw)])
    for cand, ok in zip(_candidates(raw), results):
        if ok:
            _conv_cache[hex_str] = cand
            return cand

    raise ValueError(
        "Could not convert hex into a working session. "
        "The auth key may be invalid/revoked, or the format is unsupported."
                                             )
