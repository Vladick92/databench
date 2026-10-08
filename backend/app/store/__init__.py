from __future__ import annotations

from functools import lru_cache

from backend.app import config

from .models import Chat, Message, Store, User, UsernameTaken

__all__ = ["Chat", "Message", "Store", "User", "UsernameTaken", "get_store"]


@lru_cache
def get_store() -> Store:
    """Cosmos DB when COSMOS_ENDPOINT is set (Azure), otherwise a SQLite file (local)."""
    if config.COSMOS_ENDPOINT:
        from azure.cosmos import CosmosClient
        from azure.identity import DefaultAzureCredential

        from .cosmos import CosmosStore

        client = CosmosClient(config.COSMOS_ENDPOINT, credential=DefaultAzureCredential())
        return CosmosStore(client, config.COSMOS_DATABASE)

    from .sqlite import SqliteStore

    return SqliteStore(config.APP_DB_PATH)
