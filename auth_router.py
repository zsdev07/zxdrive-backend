# auth_router.py
#
# Endpoints:
#   POST /auth/send-code      — start phone login, returns phone_code_hash
#   POST /auth/verify         — submit OTP, returns session_id or needs_2fa flag
#   POST /auth/verify-2fa     — submit cloud password if 2FA is enabled
#   POST /auth/qr/start       — start QR login, returns tg:// URL to show as QR
#   POST /auth/qr/status      — poll until QR is scanned, returns auth result
#   POST /auth/logout         — revoke session

import asyncio
import base64
import logging

from fastapi import APIRouter, HTTPException
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
    ApiIdInvalidError,
)
from telethon.tl.functions.auth import ExportLoginTokenRequest, ImportLoginTokenRequest
from telethon.tl.types import auth as tl_auth

from models import (
    SendCodeRequest, SendCodeResponse,
    VerifyCodeRequest, Verify2FARequest, AuthResponse,
    QRCodeResponse, QRStatusRequest, LogoutRequest,
    ErrorResponse,
)
import session_manager as sm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ── Phone login ───────────────────────────────────────────

@router.post("/send-code", response_model=SendCodeResponse)
async def send_code(req: SendCodeRequest):
    """
    Step 1 of phone login.
    Creates a Telethon session and sends the OTP to the user's Telegram app.
    Returns session_id (store on client) and phone_code_hash (needed for verify).
    """
    try:
        session_id, client = await sm.create_session(req.api_id, req.api_hash)
        await client.connect()

        result = await client.send_code_request(req.phone)

        return SendCodeResponse(
            session_id=session_id,
            phone_code_hash=result.phone_code_hash,
            message="Code sent to your Telegram app.",
        )

    except ApiIdInvalidError:
        raise HTTPException(400, detail="Invalid API ID or API Hash. Check my.telegram.org.")
    except FloodWaitError as e:
        raise HTTPException(429, detail=f"Too many attempts. Wait {e.seconds} seconds.")
    except Exception as e:
        logger.exception("send_code failed")
        raise HTTPException(500, detail=str(e))


@router.post("/verify", response_model=AuthResponse)
async def verify_code(req: VerifyCodeRequest):
    """
    Step 2 of phone login.
    Submits the OTP. On success returns user info and session_id.
    If 2FA is enabled, returns needs_2fa=True — client must call /verify-2fa next.
    """
    client = await sm.get_client(req.session_id, 0, "")  # api_id/hash already in session
    if client is None:
        # Session expired or server restarted without the session file
        raise HTTPException(404, detail="Session not found. Please send code again.")

    try:
        # Reconstruct the client with the correct api_id/hash from the session file.
        # (Telethon stores them in the .session SQLite so client already has them.)
        user = await client.sign_in(
            phone=req.phone,
            code=req.code,
            phone_code_hash=req.phone_code_hash,
        )

        await sm.save_session(req.session_id)   # persist auth so restarts don't expire it
        return AuthResponse(
            session_id=req.session_id,
            user_id=user.id,
            first_name=user.first_name or "",
            last_name=user.last_name,
            phone=user.phone,
            needs_2fa=False,
        )

    except SessionPasswordNeededError:
        # 2FA is enabled — tell the client to call /verify-2fa
        return AuthResponse(
            session_id=req.session_id,
            user_id=0,
            first_name="",
            needs_2fa=True,
        )
    except PhoneCodeInvalidError:
        raise HTTPException(400, detail="Incorrect code. Please try again.")
    except PhoneCodeExpiredError:
        raise HTTPException(400, detail="Code expired. Please request a new one.")
    except FloodWaitError as e:
        raise HTTPException(429, detail=f"Too many attempts. Wait {e.seconds} seconds.")
    except Exception as e:
        logger.exception("verify_code failed")
        raise HTTPException(500, detail=str(e))


@router.post("/verify-2fa", response_model=AuthResponse)
async def verify_2fa(req: Verify2FARequest):
    """
    Step 3 (only if needs_2fa was True).
    Submits the cloud password for 2FA-protected accounts.
    """
    client = await sm.get_client(req.session_id, 0, "")
    if client is None:
        raise HTTPException(404, detail="Session not found. Please start over.")

    try:
        user = await client.sign_in(password=req.password)

        await sm.save_session(req.session_id)   # persist auth so restarts don't expire it
        return AuthResponse(
            session_id=req.session_id,
            user_id=user.id,
            first_name=user.first_name or "",
            last_name=user.last_name,
            phone=user.phone,
            needs_2fa=False,
        )

    except PasswordHashInvalidError:
        raise HTTPException(400, detail="Wrong password. Please try again.")
    except FloodWaitError as e:
        raise HTTPException(429, detail=f"Too many attempts. Wait {e.seconds} seconds.")
    except Exception as e:
        logger.exception("verify_2fa failed")
        raise HTTPException(500, detail=str(e))


# ── QR login ──────────────────────────────────────────────

@router.post("/qr/start", response_model=QRCodeResponse)
async def qr_start(api_id: int, api_hash: str):
    """
    Start QR login flow.
    Returns a tg:// URL that the frontend renders as a QR code.
    The user scans it with their Telegram app on another device.
    Poll /qr/status to know when it's been scanned.
    """
    try:
        session_id, client = await sm.create_session(api_id, api_hash)
        await client.connect()

        # ExportLoginToken generates a QR login token.
        # except_ids=[] means accept login from any device.
        token_result = await client(ExportLoginTokenRequest(
            api_id=api_id,
            api_hash=api_hash,
            except_ids=[],
        ))

        if not isinstance(token_result, tl_auth.LoginToken):
            raise HTTPException(500, detail="Unexpected response from Telegram for QR login.")

        # Encode token bytes as base64url (this is the tg:// token format)
        token_b64 = base64.urlsafe_b64encode(token_result.token).decode().rstrip("=")
        qr_url = f"tg://login?token={token_b64}"

        # token_result.expires is a timezone-aware datetime, NOT a unix int.
        # Subtracting an int from it causes: "unsupported operand type(s) for -: datetime and int"
        from datetime import datetime, timezone as _tz
        now = datetime.now(_tz.utc)
        expires_dt = token_result.expires
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=_tz.utc)
        expires_in = max(0, int((expires_dt - now).total_seconds()))

        return QRCodeResponse(
            session_id=session_id,
            qr_url=qr_url,
            expires_in=expires_in,
        )

    except ApiIdInvalidError:
        raise HTTPException(400, detail="Invalid API ID or API Hash.")
    except Exception as e:
        logger.exception("qr_start failed")
        raise HTTPException(500, detail=str(e))


@router.post("/qr/status", response_model=AuthResponse)
async def qr_status(req: QRStatusRequest):
    """
    Poll this after /qr/start to check if the user has scanned the QR code.
    Returns user info when authenticated, or raises 202 if still waiting.
    Client should poll every 2 seconds with a timeout of ~60 seconds.
    """
    client = await sm.get_client(req.session_id, 0, "")
    if client is None:
        raise HTTPException(404, detail="Session not found.")

    try:
        # Check if we're already authorised (user scanned the QR)
        if await client.is_user_authorized():
            me = await client.get_me()
            await sm.save_session(req.session_id)   # persist QR auth
            return AuthResponse(
                session_id=req.session_id,
                user_id=me.id,
                first_name=me.first_name or "",
                last_name=me.last_name,
                phone=me.phone,
                needs_2fa=False,
            )
        else:
            # Still waiting — HTTP 202 Accepted tells the client to keep polling
            raise HTTPException(202, detail="Waiting for QR scan.")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("qr_status failed")
        raise HTTPException(500, detail=str(e))


# ── Logout ────────────────────────────────────────────────

@router.post("/logout")
async def logout(req: LogoutRequest):
    """Revoke the Telegram session and delete it from disk."""
    await sm.remove_session(req.session_id)
    return {"success": True, "message": "Logged out successfully."}
