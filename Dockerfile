FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/output

ENV DATABASE_URL=sqlite:////app/data/store_intelligence.db
ENV PYTHONPATH=/app

ENV PORT=8080
EXPOSE 8080

RUN chmod +x /app/scripts/docker-entrypoint.sh
CMD ["/app/scripts/docker-entrypoint.sh"]
