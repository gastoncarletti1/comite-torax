# syntax=docker/dockerfile:1
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SECRET_KEY=change-me \
    DATABASE_URL=sqlite:///instance/comite.db \
    PYTHONIOENCODING=utf-8

# Cloud Run provides $PORT
ENV PORT=8080

# Use wsgi.py as entry point for Gunicorn (DB init happens once at startup)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} wsgi:app"]
