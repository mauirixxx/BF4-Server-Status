FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY serverwatcher.py worker_agent.py discord_leader.py control_plane.py operator_notifications.py migrate_with_lock.py db.py models.py alembic.ini entrypoint.sh ./
COPY alembic ./alembic
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python3", "serverwatcher.py"]
