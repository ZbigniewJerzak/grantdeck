FROM cgr.dev/chainguard/python:latest-dev AS builder

USER root
WORKDIR /app

COPY pyproject.toml manage.py ./
COPY apps ./apps
COPY grantdeck ./grantdeck
COPY deploy ./deploy

RUN pip install --no-cache-dir --prefix=/usr/local ".[docker]" \
    && ln -s "$(echo /usr/local/lib/python3*)" /usr/local/lib/python-current \
    && PYTHONPATH=/usr/local/lib/python-current/site-packages GDK_ENV=development python manage.py collectstatic --noinput

FROM cgr.dev/chainguard/python:latest

ARG GDK_RELEASE_TAG=

ENV PYTHONUNBUFFERED=1
ENV PATH="/usr/local/bin:${PATH}"
ENV PYTHONPATH="/app:/usr/local/lib/python-current/site-packages"
ENV GDK_RELEASE_TAG=${GDK_RELEASE_TAG}

COPY --from=builder /usr/local /usr/local
COPY --from=builder --chown=65532:65532 /app /app

WORKDIR /app

EXPOSE 8000 8001

ENTRYPOINT []
CMD ["python", "deploy/start.py"]
