FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN python -m pip install --no-cache-dir -r backend/requirements.lock
COPY backend /app/backend
COPY frontend /app/frontend
COPY run_server.py /app/run_server.py
RUN python -m pip install --no-cache-dir --no-deps -e ./backend \
    && useradd --create-home --uid 10001 ekt \
    && mkdir -p /app/backend/data && chown -R ekt:ekt /app/backend/data
USER ekt
WORKDIR /app/backend
ENV CATALOG_DB=/app/backend/data/catalog.sqlite3
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/health',timeout=4)"
CMD ["python", "/app/run_server.py"]
