import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import functions

from bot.utils.helpers import fmt_device
from bot.utils.hex_session import hex_to_session_string
from config import API_HASH, API_ID, GUARD_POLL_INTERVAL


class GuardManager:
    """Keeps a session logged in and kicks every other login within ~2s."""

    def __init__(self, bot, accounts):
        self.bot = bot
        self.accounts = accounts
        self._clients = {}      # hex -> TelegramClient
        self._sessions = {}     # hex -> converted session string
        self._tasks = {}        # hex -> asyncio.Task
        self._allow_until = {}  # hex -> loop.time() deadline
        self._events = {}       # hex -> asyncio.Event
        self._known = {}        # hex -> {authorization hashes we keep}
        self._chat = {}         # hex -> chat_id for notifications

    # ------------------------------------------------------------- connection
    def _client(self, hex_str: str) -> TelegramClient:
        if hex_str not in self._clients:
            session_string = self._sessions.get(hex_str) or hex_str
            self._clients[hex_str] = TelegramClient(
                StringSession(session_string), API_ID, API_HASH
            )
        return self._clients[hex_str]

    async def _ensure_connected(self, hex_str: str) -> TelegramClient:
        if hex_str not in self._sessions:
            self._sessions[hex_str] = await hex_to_session_string(hex_str)
        client = self._client(hex_str)
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Session is expired, revoked or invalid")
        return client

    async def get_client(self, hex_str: str) -> TelegramClient:
        return await self._ensure_connected(hex_str)

    # ------------------------------------------------------------------ guard
    async def start_guard(self, hex_str: str, chat_id: int, session_string: str = None) -> dict:
        if self.is_guarded(hex_str):
            return {"status": "already"}
        if session_string:
            self._sessions[hex_str] = session_string

        client = await self._ensure_connected(hex_str)
        me = await client.get_me()
        res = await client(functions.account.GetAuthorizationsRequest())

        # Kill every existing session except this bot's own
        removed = 0
        for a in res.authorizations:
            if a.current:
                continue
            try:
                await client(functions.account.DeleteAuthorizationsRequest(hashes=[a.hash]))
                removed += 1
            except Exception:
                pass

        self._known[hex_str] = {a.hash for a in res.authorizations if a.current}
        self._chat[hex_str] = chat_id
        self._allow_until.pop(hex_str, None)
        self._events[hex_str] = asyncio.Event()
        self._tasks[hex_str] = asyncio.create_task(self._guard_loop(hex_str))

        return {
            "status": "ok",
            "removed": removed,
            "me": me,
            "devices": len(res.authorizations),
        }

    async def _guard_loop(self, hex_str: str):
        event = self._events[hex_str]
        chat_id = self._chat[hex_str]
        while True:
            # -------- Allow-login window handling --------
            while True:
                allow_until = self._allow_until.get(hex_str)
                if not allow_until:
                    break
                remaining = allow_until - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self._allow_until.pop(hex_str, None)
                    break
                try:
                    await asyncio.wait_for(event.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
                event.clear()

            if not self._allow_until.get(hex_str):
                # Window just closed -> sweep anything that logged in during it
                await self._cleanup_after_window(hex_str)
                await self.bot.send_message(
                    chat_id,
                    "🛡️ *GUARD MODE RE-ACTIVATED*\n\n"
                    "The login window has closed. Any session created during "
                    "the window has been removed. The account is protected again.",
                )

            # -------- Poll for new logins --------
            try:
                client = await self._ensure_connected(hex_str)
                res = await client(functions.account.GetAuthorizationsRequest())
                known = self._known.get(hex_str, set())
                new = [a for a in res.authorizations if not a.current and a.hash not in known]
                if new:
                    for a in new:
                        try:
                            await client(
                                functions.account.DeleteAuthorizationsRequest(hashes=[a.hash])
                            )
                        except Exception:
                            pass
                        await self.bot.send_message(
                            chat_id,
                            "⚠️ *NEW LOGIN DETECTED & TERMINATED*\n\n"
                            f"{fmt_device(a)}\n\n"
                            "⛔ Session killed within 2 seconds.",
                        )
                    self._known[hex_str] = known | {a.hash for a in new}
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await self.bot.send_message(
                    chat_id,
                    f"⚠️ *Guard error:* `{e}`\nRestarting guard connection...",
                )
                try:
                    await self._client(hex_str).disconnect()
                    await self._ensure_connected(hex_str)
                except Exception:
                    pass

            # -------- Sleep (wake early on allow-login) --------
            try:
                await asyncio.wait_for(event.wait(), timeout=GUARD_POLL_INTERVAL)
                event.clear()
            except asyncio.TimeoutError:
                pass

    async def _cleanup_after_window(self, hex_str: str):
        try:
            client = await self._ensure_connected(hex_str)
            res = await client(functions.account.GetAuthorizationsRequest())
            hashes = [a.hash for a in res.authorizations if not a.current]
            if hashes:
                await client(functions.account.DeleteAuthorizationsRequest(hashes=hashes))
            self._known[hex_str] = {a.hash for a in res.authorizations if a.current}
        except Exception:
            pass

    # ----------------------------------------------------------- allow login
    def allow_login(self, hex_str: str, seconds: int = 60) -> int:
        self._allow_until[hex_str] = asyncio.get_running_loop().time() + seconds
        ev = self._events.get(hex_str)
        if ev:
            ev.set()  # wake the loop so it sees the window
        return seconds

    # ------------------------------------------------------------- lifecycle
    def is_guarded(self, hex_str: str) -> bool:
        task = self._tasks.get(hex_str)
        return bool(task and not task.done())

    async def stop_guard(self, hex_str: str, logout: bool = True):
        task = self._tasks.pop(hex_str, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._allow_until.pop(hex_str, None)
        self._known.pop(hex_str, None)
        self._events.pop(hex_str, None)
        self._chat.pop(hex_str, None)
        self._sessions.pop(hex_str, None)

        client = self._clients.pop(hex_str, None)
        if client:
            try:
                if logout and client.is_connected():
                    await client.log_out()
                elif client.is_connected():
                    await client.disconnect()
            except Exception:
                try:
                    if client.is_connected():
                        await client.disconnect()
                except Exception:
                    pass
