from pymongo import MongoClient
from config import MONGO_URI, DB_NAME

_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)

def get_db():
    return _client[DB_NAME]
