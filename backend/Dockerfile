FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/backend

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as a non-root user. /data/storage is created and owned here so a fresh named volume inherits it.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/storage \
    && chown -R app:app /data /srv/backend

COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
