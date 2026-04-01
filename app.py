# app.py — ZX Drive Backend
#
# Run locally:   uvicorn app:app --reload --port 7860
# Hugging Face:  the Space runs this automatically via the CMD in README

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import session_manager as sm
from auth_router import router as auth_router
from files_router import router as files_router

# ── Logging ───────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────

app = FastAPI(
    title="ZX Drive API",
    description="Telegram-powered cloud storage backend. Uses Telethon + cryptg for fast MTProto transfers.",
    version="1.0.0",
    docs_url="/docs",           # Swagger UI — useful during development
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────
# Allow your React frontend (and Flutter web build) to call this API.
# Update ALLOWED_ORIGINS in production to your actual frontend URL.

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    # Defaults: localhost dev + Hugging Face Space URLs
    "http://localhost:3000,http://localhost:5173,https://*.hf.space",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten this to ALLOWED_ORIGINS in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Length"],
)

# ── Routers ───────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(files_router)

# ── Lifecycle ─────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("ZX Drive backend starting up.")
    logger.info("cryptg status: checking...")
    try:
        import cryptg
        logger.info("✅ cryptg loaded — fast C-based encryption active.")
    except ImportError:
        logger.warning(
            "⚠️  cryptg NOT available — falling back to pure-Python AES. "
            "Upload/download will be slow for large files. "
            "Add 'cryptg' to requirements.txt and redeploy."
        )


@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down — disconnecting all Telethon sessions...")
    await sm.disconnect_all()
    logger.info("All sessions disconnected.")


# ── Health check ──────────────────────────────────────────

@app.get("/", tags=["health"])
async def root():
    """Health check — returns version info."""
    try:
        import cryptg
        crypto_backend = "cryptg (fast C-based)"
    except ImportError:
        crypto_backend = "pure-Python (slow — install cryptg)"

    return {
        "service": "ZX Drive API",
        "version": "1.0.0",
        "status": "ok",
        "crypto_backend": crypto_backend,
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
