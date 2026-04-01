# files_router.py
#
# Speed optimisations vs the original HF version:
#   1. num_threads=4  — Telethon uploads 4 x 512 KB chunks in parallel.
#      This is the main reason Bot API feels fast — it does the same internally.
#   2. True streaming upload — file.file (SpooledTemporaryFile) is passed directly
#      to Telethon. No full file.read() into RAM — works for 2 GB without OOM.
#   3. iter_download with chunk_size=512KB — streaming download, no server temp file.
#   4. cryptg (compiled in Dockerfile) handles AES encryption in C, not Python.

import mimetypes
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChatWriteForbiddenError
from telethon.tl.types import DocumentAttributeFilename

from models import FileItem, ListFilesResponse, UploadResponse, DeleteResponse
import session_manager as sm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["files"])

CHUNK_SIZE = 512 * 1024   # 512 KB — matches Telegram MTProto segment size


# ── Helpers ───────────────────────────────────────────────

async def _get_authed_client(session_id: str) -> TelegramClient:
    client = await sm.get_client(session_id, 0, "")
    if client is None:
        raise HTTPException(401, detail="Session not found. Please log in again.")
    if not await client.is_user_authorized():
        raise HTTPException(401, detail="Not authenticated. Please log in again.")
    return client


def _parse_caption(caption: str) -> dict:
    try:
        parts = caption.split(" | ")
        if len(parts) >= 5 and parts[0] == "ZX Drive":
            return {
                "name":        parts[1],
                "mime_type":   parts[2],
                "size":        int(parts[3]),
                "uploaded_at": parts[4],
            }
    except Exception:
        pass
    return {}


# ── Upload ────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    session_id: str  = Form(...),
    channel_id: str  = Form(...),
    file: UploadFile = File(...),
):
    """
    Upload a file to the user's Telegram channel.
    num_threads=4 sends 4 x 512 KB chunks simultaneously — the key speed fix.
    file.file is streamed directly so 2 GB files never fully load into RAM.
    """
    client = await _get_authed_client(session_id)

    try:
        chat_id = int(channel_id)
    except ValueError:
        raise HTTPException(400, detail="channel_id must be numeric, e.g. -1001234567890")

    file_name   = file.filename or "untitled"
    mime_type   = file.content_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    uploaded_at = datetime.now(timezone.utc).isoformat()

    # Get file size without loading whole file into memory
    raw = file.file
    raw.seek(0, 2)       # seek to end
    file_size = raw.tell()
    raw.seek(0)          # rewind to start

    caption = f"ZX Drive | {file_name} | {mime_type} | {file_size} | {uploaded_at}"

    try:
        message = await client.send_file(
            chat_id,
            file=raw,                   # stream directly — no BytesIO wrapper
            caption=caption,
            force_document=True,
            part_size_kb=512,           # 512 KB per chunk
            num_threads=4,              # 4 parallel upload connections — the speed key
            attributes=[DocumentAttributeFilename(file_name=file_name)],
        )

        doc = message.document
        tg_file_id = str(doc.id) if doc else str(message.id)

        return UploadResponse(
            file_id=tg_file_id,
            message_id=message.id,
            name=file_name,
            size=file_size,
            mime_type=mime_type,
            uploaded_at=uploaded_at,
        )

    except ChatWriteForbiddenError:
        raise HTTPException(403, detail="Account cannot write to this channel. Check permissions.")
    except FloodWaitError as e:
        raise HTTPException(429, detail=f"Rate limited by Telegram. Wait {e.seconds} seconds.")
    except Exception as e:
        logger.exception("upload_file failed")
        raise HTTPException(500, detail=str(e))


# ── Download (true streaming) ─────────────────────────────

@router.get("/download/{message_id}")
async def download_file(
    message_id: int,
    session_id: str = Query(...),
    channel_id: str = Query(...),
):
    """
    Stream a file from Telegram directly to the browser.
    Chunks are decrypted by cryptg (C extension) and piped straight to HTTP response.
    No temp file written on the server. Works for files up to 2 GB.
    """
    client = await _get_authed_client(session_id)

    try:
        chat_id = int(channel_id)
    except ValueError:
        raise HTTPException(400, detail="channel_id must be numeric.")

    try:
        msg = await client.get_messages(chat_id, ids=message_id)
        if not msg or (not msg.document and not msg.photo):
            raise HTTPException(404, detail="File not found.")

        meta      = _parse_caption(msg.message or "")
        file_name = meta.get("name", f"file_{message_id}")
        mime_type = meta.get("mime_type", "application/octet-stream")
        file_size = meta.get("size")
        media     = msg.document or msg.photo

        async def stream():
            async for chunk in client.iter_download(media, request_size=CHUNK_SIZE):
                yield chunk

        headers = {
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Content-Type": mime_type,
        }
        if file_size:
            headers["Content-Length"] = str(file_size)

        return StreamingResponse(stream(), headers=headers, media_type=mime_type)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("download_file failed")
        raise HTTPException(500, detail=str(e))


# ── List files ────────────────────────────────────────────

@router.get("/list", response_model=ListFilesResponse)
async def list_files(
    session_id: str = Query(...),
    channel_id: str = Query(...),
    limit:      int = Query(50, ge=1, le=200),
    offset_id:  int = Query(0),
):
    client = await _get_authed_client(session_id)

    try:
        chat_id = int(channel_id)
    except ValueError:
        raise HTTPException(400, detail="channel_id must be numeric.")

    try:
        files = []
        async for msg in client.iter_messages(chat_id, limit=limit, offset_id=offset_id, reverse=False):
            if not msg.document:
                continue
            caption = msg.message or ""
            if not caption.startswith("ZX Drive | "):
                continue
            meta = _parse_caption(caption)
            if not meta:
                continue
            doc = msg.document
            files.append(FileItem(
                file_id=str(doc.id),
                message_id=msg.id,
                name=meta.get("name", f"file_{msg.id}"),
                size=meta.get("size", doc.size or 0),
                mime_type=meta.get("mime_type", "application/octet-stream"),
                uploaded_at=meta.get("uploaded_at", msg.date.isoformat()),
            ))

        return ListFilesResponse(files=files, total=len(files))

    except Exception as e:
        logger.exception("list_files failed")
        raise HTTPException(500, detail=str(e))


# ── Delete ────────────────────────────────────────────────

@router.delete("/{message_id}", response_model=DeleteResponse)
async def delete_file(
    message_id: int,
    session_id: str = Query(...),
    channel_id: str = Query(...),
):
    client = await _get_authed_client(session_id)

    try:
        chat_id = int(channel_id)
    except ValueError:
        raise HTTPException(400, detail="channel_id must be numeric.")

    try:
        await client.delete_messages(chat_id, [message_id])
        return DeleteResponse(success=True, message_id=message_id)
    except Exception as e:
        logger.exception("delete_file failed")
        raise HTTPException(500, detail=str(e))
