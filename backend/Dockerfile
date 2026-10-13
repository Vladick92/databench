FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY data/ data/

# Not root, here and in Azure: the same user in both, so a permission problem shows up locally. 1000 is
# the first user on the dev machine, so files written to the bind mounts (./var, ../users) are the
# owner's. HOME for the caches matplotlib and friends want to write; /app/var for a bare `docker run`
# in the local run mode (docker-compose.yml bind-mounts ./var over it).
RUN mkdir -p /app/var && chown 1000:1000 /app/var
ENV HOME=/tmp
USER 1000:1000

# BASE_DIR in backend/app/config.py resolves three parents up from this file, i.e. to /app -
# so DATA_DIR defaults to /app/data, matching where docker-compose mounts the host ./data folder.
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
