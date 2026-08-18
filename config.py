import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _int_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            out.append(int(part))
    return out


API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = (os.getenv("API_HASH") or "").strip()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()

OWNER_IDS = _int_list(os.getenv("OWNER_IDS") or os.getenv("OWNER_ID") or "")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "session_manager")

GUARD_POLL_INTERVAL = 2
ALLOW_LOGIN_SECONDS = 60
PAGE_SIZE = 5
DEVICE_PREVIEW_LIMIT = 5


def validate() -> None:
    errors = []
    if API_ID <= 0:
        errors.append("API_ID is missing or invalid")
    if not API_HASH:
        errors.append("API_HASH is missing")
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is missing")
    if not OWNER_IDS:
        errors.append("OWNER_IDS / OWNER_ID is missing")
    if errors:
        print("Config error:", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        raise SystemExit(1)
