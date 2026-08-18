from __future__ import annotations

import asyncio
from datetime import datetime

from bot.db.mongo import get_db
from config import OWNER_IDS


class AdminService:
    def __init__(self):
        self.col = get_db()["admins"]
        self.col.create_index("user_id", unique=True)

    def is_owner(self, uid: int) -> bool:
        return int(uid) in OWNER_IDS

    async def is_sudo(self, uid: int) -> bool:
        if self.is_owner(uid):
            return True
        doc = await asyncio.to_thread(self.col.find_one, {"user_id": int(uid)})
        return doc is not None

    async def add(self, uid: int, added_by: int) -> bool:
        uid = int(uid)
        if self.is_owner(uid) or await self.is_sudo(uid):
            return False
        await asyncio.to_thread(
            self.col.update_one,
            {"user_id": uid},
            {
                "$set": {
                    "user_id": uid,
                    "added_by": int(added_by),
                    "updated_at": datetime.utcnow(),
                },
                "$setOnInsert": {"created_at": datetime.utcnow()},
            },
            True,
        )
        return True

    async def remove(self, uid: int) -> bool:
        uid = int(uid)
        if self.is_owner(uid):
            return False
        result = await asyncio.to_thread(self.col.delete_one, {"user_id": uid})
        return bool(result.deleted_count)

    async def list_sudos(self) -> list:
        def _load():
            return list(self.col.find().sort("created_at", 1))

        return await asyncio.to_thread(_load)
