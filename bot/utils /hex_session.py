    """
Convert an authorized Telegram auth_key/session into a Telethon StringSession.

Supported inputs:
    1. Existing Telethon StringSession
    2. Hex-encoded Telethon StringSession
    3. Raw 256-byte auth_key
    4. DC + auth_key panel formats

For a bare 256-byte auth_key, the DC is not encoded in the key itself,
so the verifier checks the known Telegram production DCs.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.sessions import StringSession

from config import API_HASH, API_ID


log = logging.getLogger(__name__)

DC_IPV4 = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

_cache: dict[str, str] = {}


def _is_hex(value: str) -> bool:
    value = value.strip()

    return (
        bool(value)
        and len(value) % 2 == 0
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


def _looks_like_telethon(value: str) -> bool:
    """
    Check whether value is a valid Telethon StringSession.
    """
    if not value or not value.startswith("1"):
        return False

    try:
        StringSession(value)
        return True
    except Exception:
        return False


def make_string(dc_id: int, auth_key: bytes) -> str:
    """
    Build a Telethon StringSession from a DC and 256-byte auth key.
    """

    if dc_id not in DC_IPV4:
        raise ValueError(f"Unknown Telegram DC: {dc_id}")

    if len(auth_key) != 256:
        raise ValueError(
            f"auth_key must be exactly 256 bytes, got {len(auth_key)}"
        )

    session = StringSession()

    session.set_dc(
        dc_id,
        DC_IPV4[dc_id],
        443,
    )

    session.auth_key = AuthKey(auth_key)

    result = session.save()

    if not result:
        raise ValueError("Telethon failed to serialize the session")

    return result


def _extract_candidates(
    raw: bytes,
) -> list[tuple[Optional[int], bytes]]:
    """
    Extract possible (dc_id, auth_key) pairs.

    None means the input does not explicitly contain a DC.
    """

    candidates: list[tuple[Optional[int], bytes]] = []

    # ---------------------------------------------------------
    # A: bare 256-byte auth key
    # ---------------------------------------------------------
    if len(raw) == 256:
        candidates.append((None, raw))

    # ---------------------------------------------------------
    # B: 1-byte DC + 256-byte auth key
    # ---------------------------------------------------------
    elif len(raw) == 257:
        dc = raw[0]

        if dc in DC_IPV4:
            candidates.append((dc, raw[1:]))
        else:
            candidates.append((None, raw[1:]))

    # ---------------------------------------------------------
    # C: 2-byte DC + 256-byte auth key
    # ---------------------------------------------------------
    elif len(raw) == 258:
        for endian in ("little", "big"):
            dc = int.from_bytes(raw[:2], endian)

            if dc in DC_IPV4:
                candidates.append((dc, raw[2:]))

        if not candidates:
            candidates.append((None, raw[2:]))

    # ---------------------------------------------------------
    # D: 4-byte DC + 256-byte auth key
    # ---------------------------------------------------------
    elif len(raw) == 260:
        for endian in ("little", "big"):
            dc = int.from_bytes(raw[:4], endian)

            if dc in DC_IPV4:
                candidates.append((dc, raw[4:]))

        if not candidates:
            candidates.append((None, raw[4:]))

    # ---------------------------------------------------------
    # E: 4-byte DC + 1-byte test mode + 256-byte auth key
    # ---------------------------------------------------------
    elif len(raw) == 261:
        dc = int.from_bytes(raw[:4], "little")

        if dc in DC_IPV4:
            candidates.append((dc, raw[5:]))
        else:
            candidates.append((None, raw[5:]))

    else:
        raise ValueError(
            f"Unsupported hex size: {len(raw)} bytes "
            f"({len(raw) * 2} hex characters). "
            "Expected 256/257/258/260/261 bytes."
        )

    # Remove duplicate candidates
    unique = []
    seen = set()

    for dc, key in candidates:
        marker = (dc, key)

        if marker not in seen:
            seen.add(marker)
            unique.append((dc, key))

    return unique


async def _verify_dc(
    session_string: str,
    dc_id: int,
) -> Optional[str]:
    """
    Connect to one DC and verify authorization.
    """

    client = TelegramClient(
        StringSession(session_string),
        API_ID,
        API_HASH,
    )

    try:
        log.info("Checking Telegram DC%d...", dc_id)

        await asyncio.wait_for(
            client.connect(),
            timeout=15,
        )

        authorized = await client.is_user_authorized()

        if not authorized:
            log.warning(
                "DC%d connected, but the session is NOT authorized",
                dc_id,
            )
            return None

        log.info(
            "SUCCESS: auth_key is authorized on DC%d",
            dc_id,
        )

        # Save after connection in case Telethon migrated
        # the session to another DC.
        saved = client.session.save()

        return saved or session_string

    except asyncio.TimeoutError:
        log.warning(
            "DC%d timed out",
            dc_id,
        )

    except Exception as exc:
        log.warning(
            "DC%d verification failed: %s: %s",
            dc_id,
            type(exc).__name__,
            exc,
        )

    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

    return None


async def _probe(
    auth_key: bytes,
    dc_hint: Optional[int] = None,
) -> str:
    """
    Verify the auth key.

    If DC is known, check that DC first.

    If the key is bare and contains no DC information,
    check the production DCs sequentially.
    """

    if len(auth_key) != 256:
        raise ValueError(
            f"Invalid auth_key length: {len(auth_key)}"
        )

    # ---------------------------------------------------------
    # If the panel supplied a DC, don't blindly scan everything.
    # ---------------------------------------------------------
    if dc_hint in DC_IPV4:
        order = [dc_hint]
    else:
        order = list(DC_IPV4.keys())

    errors = []

    for dc_id in order:

        log.info(
            "Building session for DC%d...",
            dc_id,
        )

        try:
            candidate = make_string(
                dc_id,
                auth_key,
            )
        except Exception as exc:
            errors.append(
                f"DC{dc_id}: {exc}"
            )
            continue

        saved = await _verify_dc(
            candidate,
            dc_id,
        )

        if saved:
            return saved

    raise ValueError(
        "Auth key could not be verified. "
        + " | ".join(errors)
    )


async def hex_to_session_string(
    raw_input: str,
) -> str:
    """
    Convert either:

        Telethon StringSession
        OR
        hexadecimal panel session

    into an authorized Telethon StringSession.
    """

    raw_input = (raw_input or "").strip()

    if not raw_input:
        raise ValueError("Empty session input")

    # ---------------------------------------------------------
    # Cache
    # ---------------------------------------------------------

    if raw_input in _cache:
        return _cache[raw_input]

    # ---------------------------------------------------------
    # Already a Telethon StringSession
    # ---------------------------------------------------------

    if _looks_like_telethon(raw_input):
        _cache[raw_input] = raw_input
        return raw_input

    # ---------------------------------------------------------
    # Hex input
    # ---------------------------------------------------------

    if _is_hex(raw_input):

        raw = bytes.fromhex(raw_input)

        # -----------------------------------------------------
        # First possibility:
        # hex(UTF-8 Telethon StringSession)
        # -----------------------------------------------------

        try:
            decoded = raw.decode("ascii")

            if _looks_like_telethon(decoded):
                _cache[raw_input] = decoded
                return decoded

        except UnicodeDecodeError:
            pass

        # -----------------------------------------------------
        # Panel auth-key formats
        # -----------------------------------------------------

        candidates = _extract_candidates(raw)

        last_error: Optional[Exception] = None

        for dc_hint, auth_key in candidates:

            try:
                session = await _probe(
                    auth_key,
                    dc_hint,
                )

                _cache[raw_input] = session

                return session

            except Exception as exc:
                last_error = exc

                log.warning(
                    "Candidate verification failed: %s",
                    exc,
                )

        raise last_error or ValueError(
            "Unable to verify hexadecimal session"
        )

    # ---------------------------------------------------------
    # Final Telethon parser attempt
    # ---------------------------------------------------------

    try:
        StringSession(raw_input)

        _cache[raw_input] = raw_input

        return raw_input

    except Exception:
        raise ValueError(
            "Input is neither a valid Telethon StringSession "
            "nor a supported hexadecimal auth-key format."
        ) from None
