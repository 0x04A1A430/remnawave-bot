FROM python:3.11-slim

ARG VERSION="2.2.3"
ARG BUILD_DATE
ARG VCS_REF

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd -g 1000 app && \
    useradd -u 1000 -g 1000 -m -s /bin/bash app

COPY . .

RUN mkdir -p logs data && \
    chown -R app:app /app && \
    chown -R 1000:1000 ./logs ./data

USER app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV VERSION=${VERSION}
ENV BUILD_DATE=${BUILD_DATE}
ENV VCS_REF=${VCS_REF}

EXPOSE 8081 8082

LABEL org.opencontainers.image.title="Bedolaga RemnaWave Bot"
LABEL org.opencontainers.image.description="Telegram bot for RemnaWave VPN service"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.source="https://github.com/fr1ngg/remnawave-bedolaga-telegram-bot"
LABEL org.opencontainers.image.url="https://github.com/fr1ngg/remnawave-bedolaga-telegram-bot"
LABEL org.opencontainers.image.vendor="fr1ngg"

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8081/health || exit 1

CMD ["python", "main.py"]
