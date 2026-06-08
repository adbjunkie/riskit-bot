FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Create data dir (will be empty unless a volume is mounted)
RUN mkdir -p /app/data

ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "bot.py"]
