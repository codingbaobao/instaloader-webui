# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS python-build

WORKDIR /build/backend
COPY backend/ ./
RUN PIP_ROOT_USER_ACTION=ignore \
    python -m pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" \
        --gid app \
        --no-create-home \
        --no-log-init \
        --shell /usr/sbin/nologin \
        app

WORKDIR /app
RUN --mount=type=bind,from=python-build,source=/wheels,target=/wheels,ro \
    PIP_ROOT_USER_ACTION=ignore \
    python -m pip install --no-cache-dir --upgrade "pip>=26.1.2,<27" \
    && PIP_ROOT_USER_ACTION=ignore \
    python -m pip install \
        --no-cache-dir \
        --no-index \
        --find-links=/wheels \
        "instaloader-webui==0.1.0"
COPY --from=frontend-build /build/frontend/dist /app/static
RUN mkdir -p /data \
    && chown -R app:app /app /data

USER app
EXPOSE 8080

CMD ["uvicorn", "instaloader_webui.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
