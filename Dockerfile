FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY serverwatcher.py worker_agent.py control_plane.py db.py models.py alembic.ini entrypoint.sh ./
COPY alembic ./alembic

RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
