FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# System deps if scientific/python packages need compilation
RUN apt-get update && apt-get install -y \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency file first for better Docker layer caching
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Cloud backend WebSocket endpoint (override if needed)
ENV ARGUS_WS_URL=wss://argus-server-970096522851.asia-south1.run.app/ws/sim

# Default runtime
CMD ["python", "satellite_sim.py"]

