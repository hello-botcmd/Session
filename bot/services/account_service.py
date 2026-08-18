from __future__ import annotations

import asyncio
from datetime import datetime

from bson import ObjectId

from bot.db.mongo import get_db


class AccountService:
    def __init__(self):
        self.col = get_db()["accounts"]
        self.col.create_index("hex", unique=True)
        self.col.create_index("owner_id")
        self.col.create_index("active")

    async def upsert(self, hex_str: str, data: dict, owner_id: int | None = None):
        payload = dict(data)
        payload["updated_at"] = datetime.utcnow()
        if owner_id is not None:
            payload["owner_id"] = owner_id

        insert = {"hex": hex_str, "created_at": datetime.utcnow()}
        if owner_id is not None:
            insert["owner_id"] = owner_id

        await asyncio.to_thread(
            self.col.update_one,
            {"hex": hex_str},
            {"$set": payload, "$setOnInsert": insert},
            True,
        )

    async def get(self, hex_str: str):
        return await asyncio.to_thread(self.col.find_one, {"hex": hex_str})

    async def get_by_id(self, oid: str, owner_id: int | None = None):
        try:
            query = {"_id": ObjectId(oid)}
        except Exception:
            return None
        if owner_id is not None:
            query["owner_id"] = owner_id
        return await asyncio.to_thread(self.col.find_one, query)

    def _find(self, query: dict) -> list:
        return list(self.col.find(query).sort("updated_at", -1))

    async def all_for(self, owner_id: int) -> list:
        return await asyncio.to_thread(self._find, {"owner_id": owner_id})

    async def list_active(self) -> list:
        return await asyncio.to_thread(self._find, {"active": True})

    async def delete(self, hex_str: str, owner_id: int | None = None):
        query = {"hex": hex_str}
        if owner_id is not None:
            query["owner_id"] = owner_id
        await asyncio.to_thread(self.col.delete_one, query)

    async def set_active(self, hex_str: str, active: bool, chat_id: int | None = None):
        fields = {"active": active, "updated_at": datetime.utcnow()}
        if chat_id is not None:
            fields["chat_id"] = chat_id
        await asyncio.to_thread(
            self.col.update_one,
            {"hex": hex_str},
            {"$set": fields},
        )
