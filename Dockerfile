# ------------------------------
# Stage 1: Base
# ------------------------------
FROM python:3.11-slim

# Install system dependencies (ffmpeg required for yt_dlp merges)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements (so Docker caching works efficiently)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose Flask port
EXPOSE 5000

# Set environment (disable flask reloader in Docker)
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Run the app
CMD ["python", "main.py"]
