# ============================================================
# SESLY BOT - DOCKERFILE
# ============================================================
# Linux container for meeting bot automation
# Includes: Chromium, FFmpeg, PulseAudio, Xvfb

FROM python:3.10-slim

LABEL maintainer="Sesly Bot"
LABEL description="AI Meeting Assistant Bot"

# ============================================================
# SYSTEM DEPENDENCIES
# ============================================================

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Browser
    chromium \
    chromium-driver \
    # Audio
    ffmpeg \
    pulseaudio \
    # Virtual Display
    xvfb \
    x11-utils \
    # Browser dependencies
    fonts-liberation \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxcomposite1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    # Utilities
    wget \
    curl \
    gnupg \
    ca-certificates \
    # Klavye/clipboard otomasyonu (Teams CKEditor için)
    xdotool \
    xclip \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# PYTHON ENVIRONMENT
# ============================================================

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements-linux.txt .
RUN pip install --no-cache-dir -r requirements-linux.txt

# Install Playwright browsers
RUN playwright install chromium --with-deps

# ============================================================
# APPLICATION CODE
# ============================================================

COPY . .

# Create necessary directories
RUN mkdir -p logs data temp_reports

# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV PYTHONPATH=/app
ENV DISPLAY=:99

# ============================================================
# ENTRYPOINT
# ============================================================

COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

EXPOSE 9000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "server.py"]
