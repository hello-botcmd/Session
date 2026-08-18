from __future__ import annotations

import asyncio
import logging
from html import escape

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl import functions

from bot.utils.helpers import fmt_device
from bot.utils.hex_session import connect_from_raw
from config import API_HASH, API_ID, GUARD_POLL_INTERVAL

log = logging.getLogger(__name__)


class GuardManager:
    """One live client per session. Kicks every other login on a 2s poll."""

    def __init__(self, bot, accounts):
        self.bot = bot
        self.accounts = accounts
        self._clients: dict[str, TelegramClient] = {}
        self._sessions: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._allow_until: dict[str, float] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._chat: dict[str, int] = {}

    async def get_client(self, hex_str: str) -> TelegramClient:
        return await self._ensure_connected(hex_str)

    async def _ensure_connected(self, hex_str: str) -> TelegramClient:
        client = self._clients.get(hex_str)
        if client is None:
            raw = self._sessions.get(hex_str) or hex_str
            client = await connect_from_raw(raw)
            self._clients[hex_str] = client
            saved = client.session.save() if client.session else ""
            if saved:
                self._sessions[hex_str] = saved
            return client

        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            raise ValueError("Session is expired, revoked or invalid")
        return client

    async def start_guard(
        self,
        hex_str: str,
        chat_id: int,
        session_string: str | None = None,
        client: TelegramClient | None = None,
    ) -> dict:
        if self.is_guarded(hex_str):
            if client and client is not self._clients.get(hex_str):
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return {"status": "already", "removed": 0}

        if session_string:
            self._sessions[hex_str] = session_string
        if client is not None:
            self._clients[hex_str] = client
            saved = session_string or (client.session.save() if client.session else "")
            if saved:
                self._sessions[hex_str] = saved

        live = await self._ensure_connected(hex_str)
        me = await live.get_me()
        removed = 0
        try:
            res = await live(functions.account.GetAuthorizationsRequest())
            for auth in res.authorizations:
                if auth.current:
                    continue
                try:
                    await live(functions.account.ResetAuthorizationRequest(hash=auth.hash))
                    removed += 1
                except Exception:
                    pass
        except Exception:
            pass

        self._chat[hex_str] = chat_id
        self._allow_until.pop(hex_str, None)
        self._events[hex_str] = asyncio.Event()
        self._tasks[hex_str] = asyncio.create_task(
            self._guard_loop(hex_str),
            name=f"guard:{hex_str[:8]}",
        )
        try:
            await self.accounts.set_active(hex_str, True, chat_id=chat_id)
        except Exception:
            pass
        return {"status": "ok", "removed": removed, "me": me}

    async def _notify(self, chat_id: int, text: str) -> None:
        try:
            await self.bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning("guard notify failed: %s", exc)

    async def _sweep(self, hex_str: str) -> None:
        client = await self._ensure_connected(hex_str)
        res = await client(functions.account.GetAuthorizationsRequest())
        chat_id = self._chat[hex_str]
        for auth in res.authorizations:
            if auth.current:
                continue
            try:
                await client(functions.account.ResetAuthorizationRequest(hash=auth.hash))
            except Exception:
                continue
            await self._notify(
                chat_id,
                "⚠️ <b>NEW LOGIN DETECTED &amp; TERMINATED</b>\n\n"
                f"{fmt_device(auth)}\n\n"
                "⛔ Session killed within 2 seconds.",
            )

    async def _cleanup_after_window(self, hex_str: str) -> None:
        try:
            client = await self._ensure_connected(hex_str)
            res = await client(functions.account.GetAuthorizationsRequest())
            for auth in res.authorizations:
                if auth.current:
                    continue
                try:
                    await client(functions.account.ResetAuthorizationRequest(hash=auth.hash))
                except Exception:
                    pass
        except Exception:
            pass

    async def _guard_loop(self, hex_str: str):
        event = self._events[hex_str]
        chat_id = self._chat[hex_str]
        try:
            while True:
                allow_until = self._allow_until.get(hex_str)
                if allow_until:
                    remaining = allow_until - asyncio.get_running_loop().time()
                    if remaining > 0:
                        try:
                            await asyncio.wait_for(event.wait(), timeout=remaining)
                            event.clear()
                            continue
                        except asyncio.TimeoutError:
                            pass
                    self._allow_until.pop(hex_str, None)
                    await self._cleanup_after_window(hex_str)
                    await self._notify(
                        chat_id,
                        "🛡️ <b>GUARD MODE RE-ACTIVATED</b>\n\n"
                        "The login window has closed. Any session created during "
                        "the window has been removed.",
                    )

                try:
                    await self._sweep(hex_str)
                except asyncio.CancelledError:
                    raise
                except FloodWaitError as exc:
                    await asyncio.sleep(int(getattr(exc, "seconds", 1)) + 1)
                    continue
                except Exception as exc:
                    await self._notify(
                        chat_id,
                        f"⚠️ <b>Guard error:</b> <code>{escape(str(exc))}</code>\n"
                        "Reconnecting...",
                    )
                    try:
                        old = self._clients.pop(hex_str, None)
                        if old:
                            await old.disconnect()
                    except Exception:
                        pass
                    try:
                        await self._ensure_connected(hex_str)
                    except Exception:
                        await asyncio.sleep(5)

                try:
                    await asyncio.wait_for(event.wait(), timeout=GUARD_POLL_INTERVAL)
                    event.clear()
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    def allow_login(self, hex_str: str, seconds: int = 60) -> int:
        self._allow_until[hex_str] = asyncio.get_running_loop().time() + seconds
        ev = self._events.get(hex_str)
        if ev:
            ev.set()
        return seconds

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
        try:
            await self.accounts.set_active(hex_str, False)
        except Exception:
            pass

    async def shutdown(self, logout: bool = False):
        for hex_str in list(self._tasks):
            await self.stop_guard(hex_str, logout=logout)
