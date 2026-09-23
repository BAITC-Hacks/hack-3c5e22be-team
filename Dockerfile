FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock
COPY backend /app/backend
COPY frontend /app/frontend
COPY start_hosted.py /app/start_hosted.py
RUN pip install --no-cache-dir --no-deps ./backend
ENV PYTHONUNBUFFERED=1 EKT_DEPLOY_DEMO=true
EXPOSE 8080
CMD ["python", "start_hosted.py"]
