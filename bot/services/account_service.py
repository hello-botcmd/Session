from datetime import datetime

from bson import ObjectId

from bot.db.mongo import get_db


class AccountService:
    """MongoDB storage for verified / guarded sessions."""

    def __init__(self):
        self.col = get_db()["accounts"]

    def upsert(self, hex_str: str, data: dict):
        """Insert or update an account, keyed by its session hex."""
        data = dict(data)
        data["updated_at"] = datetime.utcnow()
        self.col.update_one(
            {"hex": hex_str},
            {
                "$set": data,
                "$setOnInsert": {"hex": hex_str, "created_at": datetime.utcnow()},
            },
            upsert=True,
        )

    def get(self, hex_str: str):
        return self.col.find_one({"hex": hex_str})

    def get_by_id(self, oid: str):
        try:
            return self.col.find_one({"_id": ObjectId(oid)})
        except Exception:
            return None

    def all(self) -> list:
        return list(self.col.find().sort("updated_at", -1))

    def delete(self, hex_str: str):
        self.col.delete_one({"hex": hex_str})

    def set_active(self, hex_str: str, active: bool):
        self.col.update_one(
            {"hex": hex_str},
            {"$set": {"active": active, "updated_at": datetime.utcnow()}},
        )
