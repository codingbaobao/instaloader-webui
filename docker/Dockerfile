FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS python-build

WORKDIR /build/backend
COPY backend/ ./
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system app \
    && adduser --system --ingroup app app

WORKDIR /app
COPY --from=python-build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels
COPY --from=frontend-build /build/frontend/dist /app/static
RUN mkdir -p /data \
    && chown -R app:app /app /data

USER app
EXPOSE 8080

CMD ["uvicorn", "instaloader_webui.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
