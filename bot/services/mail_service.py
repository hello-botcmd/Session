from __future__ import annotations

import asyncio
from datetime import datetime

from bot.db.mongo import get_db


class MailService:
    def __init__(self):
        self.col = get_db()["mails"]
        self.col.create_index("user_id", unique=True)

    async def set(self, uid: int, email: str, app_password: str):
        await asyncio.to_thread(
            self.col.update_one,
            {"user_id": int(uid)},
            {
                "$set": {
                    "user_id": int(uid),
                    "email": email.strip(),
                    "app_password": app_password.strip(),
                    "updated_at": datetime.utcnow(),
                },
                "$setOnInsert": {"created_at": datetime.utcnow()},
            },
            True,
        )

    async def get(self, uid: int):
        return await asyncio.to_thread(self.col.find_one, {"user_id": int(uid)})

    async def delete(self, uid: int) -> bool:
        result = await asyncio.to_thread(self.col.delete_one, {"user_id": int(uid)})
        return bool(result.deleted_count)
