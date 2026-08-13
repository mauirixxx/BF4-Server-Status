FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY serverwatcher.py .
COPY maps.json .
COPY servers.example.json .

CMD ["python3", "serverwatcher.py"]
