FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY etalase ./etalase
COPY samples ./samples

# Cloud Run sends traffic to $PORT
CMD exec uvicorn etalase.api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers
