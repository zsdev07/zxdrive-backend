# models.py — request and response schemas

from pydantic import BaseModel
from typing import Optional


# ── Auth ──────────────────────────────────────────────────

class SendCodeRequest(BaseModel):
    api_id: int
    api_hash: str
    phone: str                  # E.164 format e.g. +919876543210


class SendCodeResponse(BaseModel):
    session_id: str             # opaque token — client stores this
    phone_code_hash: str        # needed for verify step
    message: str


class VerifyCodeRequest(BaseModel):
    session_id: str
    phone: str
    phone_code_hash: str
    code: str                   # 5-digit OTP from Telegram app


class Verify2FARequest(BaseModel):
    session_id: str
    password: str


class AuthResponse(BaseModel):
    session_id: str
    user_id: int
    first_name: str
    last_name: Optional[str] = None
    phone: Optional[str] = None
    needs_2fa: bool = False


class QRCodeResponse(BaseModel):
    session_id: str
    qr_url: str                 # tg://login?token=... — render this as QR on frontend
    expires_in: int             # seconds until QR expires


class QRStatusRequest(BaseModel):
    session_id: str


class LogoutRequest(BaseModel):
    session_id: str


# ── Files ─────────────────────────────────────────────────

class FileItem(BaseModel):
    file_id: str                # Telegram file ID
    message_id: int             # message ID in the channel
    name: str
    size: int                   # bytes
    mime_type: str
    uploaded_at: str            # ISO 8601


class ListFilesResponse(BaseModel):
    files: list[FileItem]
    total: int


class DeleteRequest(BaseModel):
    session_id: str
    message_id: int


class DeleteResponse(BaseModel):
    success: bool
    message_id: int


class UploadResponse(BaseModel):
    file_id: str
    message_id: int
    name: str
    size: int
    mime_type: str
    uploaded_at: str


# ── Error ─────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    code: Optional[str] = None
