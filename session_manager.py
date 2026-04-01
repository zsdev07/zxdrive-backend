# session_manager.py
#
# Each user gets their own TelegramClient identified by a session_id (UUID).
#
# FIX 1 — api_id/api_hash stored per session:
#   When restoring from disk after a server restart, Telethon needs the original
#   api_id and api_hash. Previously they were passed as 0/"" which silently
#   created a broken client — causing instant "session expired" on every restart.
#
# FIX 2 — StringSession used as persistent store:
#   File-based .session files disappear on Render's ephemeral filesystem on every
#   deploy/restart. StringSession serialises the auth into a string that you can
#   store anywhere (env var, DB, etc.). Here we save it to a small .json file
#   alongside the credentials so restores always work.
#
# FIX 3 — Auto-reconnect on is_connected() check:
#   Idle TCP connections are dropped by Render's proxy after ~60 s of silence.
#   Every get_client() call now ensures the client is connected before returning.

import os
import json
import uuid
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

SESSIONS_DIR = "./sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

# In-memory registry: session_id → TelegramClient
_clients: dict[str, TelegramClient] = {}


# ── Internal helpers ──────────────────────────────────────

def _meta_path(session_id: str) -> str:
    """JSON file that stores api_id, api_hash, and the serialised StringSession."""
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def _save_meta(session_id: str, api_id: int, api_hash: str, client: TelegramClient) -> None:
    """Persist credentials + current session string to disk."""
    try:
        session_str = client.session.save()
        with open(_meta_path(session_id), "w") as f:
            json.dump({"api_id": api_id, "api_hash": api_hash, "session": session_str}, f)
    except Exception:
        logger.exception(f"Failed to save session meta for {session_id}")


def _load_meta(session_id: str) -> Optional[dict]:
    """Load credentials + session string from disk. Returns None if missing."""
    path = _meta_path(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        logger.exception(f"Failed to load session meta for {session_id}")
        return None


# ── Public API ────────────────────────────────────────────

async def create_session(api_id: int, api_hash: str) -> tuple[str, TelegramClient]:
    """
    Create a new Telethon client with a fresh StringSession.
    Returns (session_id, client). Client is NOT yet connected — caller must connect.
    """
    session_id = str(uuid.uuid4())
    client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
        connection_retries=5,
        retry_delay=1,
    )
    _clients[session_id] = client

    # Store api_id/api_hash so we can restore this client after a server restart.
    # Session string is empty at this point; it will be populated after sign_in.
    _save_meta(session_id, api_id, api_hash, client)

    logger.info(f"Created session {session_id}")
    return session_id, client


async def save_session(session_id: str) -> None:
    """
    Call this after a successful sign_in / QR auth to persist the
    authenticated session string to disk.
    """
    client = _clients.get(session_id)
    if client is None:
        return
    meta = _load_meta(session_id)
    if meta is None:
        return
    _save_meta(session_id, meta["api_id"], meta["api_hash"], client)
    logger.info(f"Saved authenticated session {session_id}")


async def get_client(session_id: str, api_id: int = 0, api_hash: str = "") -> Optional[TelegramClient]:
    """
    Return an existing connected client, or restore from disk after a restart.
    Returns None if the session is unknown / deleted.
    """
    # Already in memory
    if session_id in _clients:
        client = _clients[session_id]
        if not client.is_connected():
            await client.connect()   # FIX 3: reconnect dropped idle connection
        return client

    # Try to restore from disk using saved credentials + session string
    meta = _load_meta(session_id)
    if meta is None:
        return None

    stored_api_id   = meta.get("api_id") or api_id
    stored_api_hash = meta.get("api_hash") or api_hash
    session_str     = meta.get("session", "")

    if not stored_api_id or not stored_api_hash:
        logger.warning(f"Cannot restore {session_id}: missing api_id/api_hash in meta")
        return None

    client = TelegramClient(
        StringSession(session_str),   # FIX 1+2: restore with real credentials & session
        stored_api_id,
        stored_api_hash,
        connection_retries=5,
        retry_delay=1,
    )
    await client.connect()
    _clients[session_id] = client
    logger.info(f"Restored session {session_id} from disk")
    return client


async def remove_session(session_id: str) -> None:
    """Disconnect, log out from Telegram, and delete all session data."""
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

    meta_file = _meta_path(session_id)
    if os.path.exists(meta_file):
        os.remove(meta_file)
        logger.info(f"Deleted session meta for {session_id}")


async def disconnect_all() -> None:
    """Gracefully disconnect all clients on shutdown."""
    for client in list(_clients.values()):
        try:
            await client.disconnect()
        except Exception:
            pass
    _clients.clear()
