from pymongo import MongoClient

from config import MONGO_URI, DB_NAME

_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)


def get_db():
    return _client[DB_NAME]
