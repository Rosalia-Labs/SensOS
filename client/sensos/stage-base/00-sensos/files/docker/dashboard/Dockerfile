FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DASHBOARD_BIND=0.0.0.0 \
    DASHBOARD_INTERNAL_PORT=8090 \
    DASHBOARD_WORKERS=2

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates

EXPOSE 8090

CMD ["sh", "-c", "gunicorn --bind ${DASHBOARD_BIND}:${DASHBOARD_INTERNAL_PORT} --workers ${DASHBOARD_WORKERS} --threads 2 --timeout 30 app:app"]
