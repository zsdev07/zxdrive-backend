FROM python:3.11-slim

WORKDIR /app

# gcc + python3-dev + libssl-dev — all needed to compile cryptg (C extension).
# Without cryptg, Telethon falls back to pure-Python AES → 10-20x slower uploads/downloads.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc python3-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Verify cryptg compiled successfully — fail the build early if not
RUN python -c "import cryptg; print('cryptg OK')"

COPY . .

RUN mkdir -p sessions

# Render injects $PORT — default 10000. HF used 7860.
# We read it at runtime so the same image works on both platforms.
EXPOSE 10000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"]
