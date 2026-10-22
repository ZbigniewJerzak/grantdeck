FROM cgr.dev/chainguard/python:latest-dev AS builder

USER root
WORKDIR /app

COPY pyproject.toml manage.py ./
COPY apps ./apps
COPY grantdeck ./grantdeck
COPY deploy ./deploy

RUN pip install --no-cache-dir --target=/opt/grantdeck-python ".[docker]" \
    && PYTHONPATH=/opt/grantdeck-python GDK_ENV=development python manage.py collectstatic --noinput

FROM cgr.dev/chainguard/python:latest

ARG GDK_RELEASE_TAG=

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/app:/opt/grantdeck-python"
ENV GDK_RELEASE_TAG=${GDK_RELEASE_TAG}

COPY --from=builder /opt/grantdeck-python /opt/grantdeck-python
COPY --from=builder --chown=65532:65532 /app /app

WORKDIR /app

EXPOSE 8000 8001

ENTRYPOINT []
CMD ["python", "deploy/start.py"]
