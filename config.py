import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "session_manager"

GUARD_EMAIL = os.getenv("GUARD_EMAIL", "")
GUARD_EMAIL_APP_PASSWORD = os.getenv("GUARD_EMAIL_APP_PASSWORD", "")

GUARD_POLL_INTERVAL = 2       # seconds -> kicks new logins within ~2s
ALLOW_LOGIN_SECONDS = 60      # login window
PAGE_SIZE = 5                 # accounts per page
