FROM python:3.11-slim

WORKDIR /app

# Optional: MJPEG from mounted CCTV clips (local docker compose). Cloud deploy leaves this false.
ARG WITH_OPENCV=false
RUN if [ "$WITH_OPENCV" = "true" ]; then \
    apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "opencv-python-headless>=4.9.0"; \
    fi

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY . .

RUN mkdir -p /app/data /app/output

ENV DATABASE_URL=sqlite:////app/data/store_intelligence.db
ENV PYTHONPATH=/app

ENV PORT=8080
EXPOSE 8080

RUN chmod +x /app/scripts/docker-entrypoint.sh
CMD ["/app/scripts/docker-entrypoint.sh"]
