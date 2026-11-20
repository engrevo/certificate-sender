FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libraqm0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
# Single worker keeps the in-memory job list consistent; threads handle concurrency.
CMD gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT} "app:create_app()"
