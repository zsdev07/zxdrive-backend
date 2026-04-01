---
title: ZX Drive API
emoji: ☁️
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# ZX Drive Backend API

Telegram-powered cloud storage backend built with **FastAPI + Telethon**.  
Supports phone number login, QR login, 2FA, file upload/download up to **2 GB**, and file listing.

## Deploy to Hugging Face Spaces

### 1. Create a new Space

1. Go to [huggingface.co/new-space](https://huggingface.co/new-space)
2. Choose **Docker** as the SDK
3. Set visibility to **Public** (or Private if you want)
4. Clone the Space repo and copy all files from this folder into it

### 2. File structure required

```
your-space/
  app.py
  auth_router.py
  files_router.py
  session_manager.py
  models.py
  requirements.txt
  Dockerfile
  README.md
```

### 3. Add the Dockerfile

Create a `Dockerfile` in the root:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install build tools needed for cryptg (C extension)
RUN apt-get update && apt-get install -y gcc libssl-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create sessions directory (persisted across restarts via HF persistent storage)
RUN mkdir -p sessions

# Hugging Face Spaces exposes port 7860
EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
```

### 4. Push and deploy

```bash
git add .
git commit -m "initial deploy"
git push
```

Hugging Face will build the Docker image and deploy automatically.  
Your API will be live at: `https://YOUR-USERNAME-YOUR-SPACE-NAME.hf.space`

---

## API Reference

### Base URL
```
https://YOUR-USERNAME-YOUR-SPACE-NAME.hf.space
```

### Auth — Phone Login

**Step 1: Send OTP**
```
POST /auth/send-code
{
  "api_id": 12345678,
  "api_hash": "your_api_hash",
  "phone": "+919876543210"
}
→ { "session_id": "uuid", "phone_code_hash": "...", "message": "..." }
```

**Step 2: Verify OTP**
```
POST /auth/verify
{
  "session_id": "uuid-from-step1",
  "phone": "+919876543210",
  "phone_code_hash": "hash-from-step1",
  "code": "12345"
}
→ { "session_id": "...", "user_id": 123, "first_name": "...", "needs_2fa": false }
```

**Step 3 (only if needs_2fa = true): Submit 2FA password**
```
POST /auth/verify-2fa
{
  "session_id": "uuid",
  "password": "your-cloud-password"
}
→ { "session_id": "...", "user_id": 123, "first_name": "...", "needs_2fa": false }
```

### Auth — QR Login

**Start QR flow**
```
POST /auth/qr/start?api_id=12345678&api_hash=your_hash
→ { "session_id": "uuid", "qr_url": "tg://login?token=...", "expires_in": 30 }
```
Render `qr_url` as a QR code. User scans it with Telegram on another device.

**Poll for scan**
```
POST /auth/qr/status
{ "session_id": "uuid" }
→ 202 if waiting, 200 with user info when scanned
```

### Files

**Upload**
```
POST /files/upload
Content-Type: multipart/form-data
Fields: session_id, channel_id, file
→ { "file_id": "...", "message_id": 123, "name": "...", "size": 1048576, ... }
```

**Download (streaming)**
```
GET /files/download/{message_id}?session_id=uuid&channel_id=-1001234567890
→ file stream with Content-Disposition header
```

**List files**
```
GET /files/list?session_id=uuid&channel_id=-1001234567890&limit=50
→ { "files": [...], "total": 12 }
```

**Delete**
```
DELETE /files/{message_id}?session_id=uuid&channel_id=-1001234567890
→ { "success": true, "message_id": 123 }
```

### Logout
```
POST /auth/logout
{ "session_id": "uuid" }
```

---

## Important Notes

### cryptg — make sure it's installed
`cryptg` replaces Telethon's pure-Python AES with a fast C extension.  
Without it, a 2 GB upload/download that takes 2 minutes will take 30–40 minutes.  
It's already in `requirements.txt` — the Dockerfile installs `gcc` to compile it.

### Sessions
- Each user's session is saved as a `.session` file in `./sessions/`
- Sessions persist across server restarts
- On Hugging Face free tier, the container may restart periodically — sessions are restored from disk automatically

### Channel ID
- The user needs a Telegram channel or group where files are stored
- The channel ID is in the format `-1001234567890`
- The logged-in account must have permission to send messages to that channel

### Rate limits
- Telegram enforces rate limits (FloodWaitError)
- The API returns HTTP 429 with the wait time in seconds when this happens
- For heavy use, consider running multiple backend instances with different accounts
