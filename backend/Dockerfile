FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY data/ data/

# BASE_DIR in backend/app/config.py resolves three parents up from this file, i.e. to /app -
# so DATA_DIR defaults to /app/data, matching where docker-compose mounts the host ./data folder.
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
