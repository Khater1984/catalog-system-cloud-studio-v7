FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_ENV=production STORAGE_BACKEND=drive \
    DATA_DIR=/tmp/catalog_data CACHE_DIR=/tmp/catalog_cache PORT=8080
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN rm -rf tests data venv __pycache__
# one process, several threads: the background extraction runs as threads of the same process
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
