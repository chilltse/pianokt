FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ ./
RUN pip install --no-cache-dir --no-deps . && useradd -m app
USER app
CMD ["uvicorn", "pianokt_backend.api:app", "--host", "0.0.0.0", "--port", "8080"]
