# session_manager.py
#
# Each user gets their own TelegramClient identified by a session_id (UUID).
# Sessions are kept in memory while the server is running.
# The underlying Telethon .session file is saved to disk under ./sessions/
# so sessions survive a server restart (important on Hugging Face Spaces
# which restart containers periodically).

import os
import uuid
import asyncio
import logging
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

SESSIONS_DIR = "./sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# In-memory registry: session_id → TelegramClient
_clients: dict[str, TelegramClient] = {}


def _session_path(session_id: str) -> str:
    """Return the .session file path for a given session_id."""
    return os.path.join(SESSIONS_DIR, session_id)


async def create_session(api_id: int, api_hash: str) -> tuple[str, TelegramClient]:
    """
    Create a new Telethon client with a fresh session.
    Returns (session_id, client).
    The client is NOT yet connected — caller must connect it.
    """
    session_id = str(uuid.uuid4())
    client = TelegramClient(
        _session_path(session_id),
        api_id,
        api_hash,
        # Use the system proxy if set; otherwise connect directly.
        # connection_retries=5 handles transient network issues on HF Spaces.
        connection_retries=5,
        retry_delay=1,
    )
    _clients[session_id] = client
    logger.info(f"Created session {session_id}")
    return session_id, client


async def get_client(session_id: str, api_id: int, api_hash: str) -> Optional[TelegramClient]:
    """
    Return an existing connected client, or restore it from disk if the
    server restarted. Returns None if the session file doesn't exist.
    """
    # Already in memory and connected
    if session_id in _clients:
        client = _clients[session_id]
        if not client.is_connected():
            await client.connect()
        return client

    # Try to restore from disk
    session_file = _session_path(session_id) + ".session"
    if not os.path.exists(session_file):
        return None

    client = TelegramClient(
        _session_path(session_id),
        api_id,
        api_hash,
        connection_retries=5,
        retry_delay=1,
    )
    await client.connect()
    _clients[session_id] = client
    logger.info(f"Restored session {session_id} from disk")
    return client


async def remove_session(session_id: str) -> None:
    """Disconnect and delete a session entirely."""
    client = _clients.pop(session_id, None)
    if client:
        try:
            await client.log_out()
        except Exception:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass

    # Delete the .session file from disk
    session_file = _session_path(session_id) + ".session"
    if os.path.exists(session_file):
        os.remove(session_file)
        logger.info(f"Deleted session file for {session_id}")


async def disconnect_all() -> None:
    """Gracefully disconnect all clients on shutdown."""
    for session_id, client in list(_clients.items()):
        try:
            await client.disconnect()
        except Exception:
            pass
    _clients.clear()
