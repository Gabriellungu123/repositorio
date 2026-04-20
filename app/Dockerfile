FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema (si necesitas bcrypt/cryptography)
RUN apt-get update && apt-get install -y build-essential libssl-dev libffi-dev default-libmysqlclient-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "server.py"]
