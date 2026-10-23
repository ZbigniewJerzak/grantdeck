# GrantDeck

GrantDeck provides self-service bearer tokens and an Envoy-compatible authorization endpoint for a protected AI API. API traffic stays on the Envoy data path; GrantDeck handles identity, projects, authorization decisions, quotas, and usage reporting.

## Overview

- Users authenticate with OpenID Connect and create expiring bearer tokens.
- Tokens belong to projects. Project administrators manage membership; superusers manage projects and limit classes.
- `GDK_AUTHZ_API_ENDPOINT` defines the single public API URL prefix protected by GrantDeck.
- Each project has a limit class containing per-model daily token and optional concurrency limits.
- GrantDeck generates `AIGatewayRoute`, `BackendTrafficPolicy`, and `SecurityPolicy` resources and applies them to its Kubernetes namespace.
- Envoy ALS records provide daily token usage by user, project, and model for the UI and Prometheus.

## Runtime services

The container image runs in two roles:

- Django/Gunicorn on port `8000` for the portal, admin, authorization, health, and metrics endpoints.
- `python -m apps.usage.receiver` on gRPC port `8001` for Envoy Access Log Service streams.

| Path | Purpose |
| --- | --- |
| `/authz/check/<protected-path>` | Envoy external authorization |
| `/healthz/` | Process liveness |
| `/readyz/` | Database readiness |
| `/metrics` | Authenticated Prometheus metrics |

## Configuration

Production mode is the default. Set `GDK_ENV=development` for SQLite, `DEBUG=True`, and development-only defaults.

Required in production:

| Variable | Purpose |
| --- | --- |
| `GDK_SECRET_KEY` | Django cryptographic secret |
| `GDK_DATABASE_URL` | PostgreSQL connection URL |
| `GDK_ALLOWED_HOSTS` | Comma-separated accepted host names |
| `GDK_AUTHZ_API_ENDPOINT` | Public protected API prefix, for example `https://api.example.com/v1` |
| `GDK_OIDC_ENDPOINT` | OpenID Connect issuer |
| `GDK_OIDC_CLIENT_ID` | OpenID Connect client ID |
| `GDK_OIDC_CLIENT_SECRET` | OpenID Connect client secret |
| `GDK_AIGATEWAY_GATEWAY_NAME` | Parent Gateway name |
| `GDK_AIGATEWAY_GATEWAY_NAMESPACE` | Parent Gateway namespace |
| `GDK_AIGATEWAY_GATEWAY_SECTION` | Public listener section |
| `GDK_AIGATEWAY_GATEWAY_INTERNAL_SECTION` | Internal listener section |
| `GDK_AIGATEWAY_MODELS_OWNER` | Owner emitted on AI Gateway routes |
| `GDK_AIGATEWAY_MODEL_BACKENDS` | JSON mapping from model names to backend references |

Optional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GDK_CSRF_TRUSTED_ORIGINS` | empty | Trusted CSRF origins |
| `GDK_SCRIPT_NAME` | empty | URL prefix for GrantDeck itself |
| `GDK_OIDC_ADMIN_USERNAME` | empty | OIDC user promoted to superuser |
| `GDK_BRAND_NAME` | `GrantDeck` | Display name |
| `GDK_SSO_LOGIN_BUTTON_TEXT` | `Login with SSO` | SSO button text |
| `GDK_STATIC_TOKEN_MAX_LIFETIME_DAYS` | `183` | Maximum bearer-token lifetime |
| `GDK_PROJECT_JOIN_ATTEMPT_LIMIT` | `5` | Failed join attempts per window |
| `GDK_PROJECT_JOIN_ATTEMPT_WINDOW_SECONDS` | `600` | Join-attempt window |
| `GDK_SESSION_COOKIE_SECURE` | `false` | Secure session cookies |
| `GDK_CSRF_COOKIE_SECURE` | `false` | Secure CSRF cookies |
| `GDK_CSRF_COOKIE_HTTPONLY` | `false` | HTTP-only CSRF cookies |
| `GDK_LOG_LEVEL` | `INFO` | Application log level |
| `GDK_USAGE_METRICS_TOKEN` | unset | Bearer token for `/metrics`; unset disables it |
| `GDK_ALS_PORT` | `8001` | ALS receiver port |
| `GDK_ALS_MAX_WORKERS` | `256` | Concurrent ALS streams per receiver |

`GDK_AUTHZ_API_ENDPOINT` must be an absolute HTTP(S) URL without credentials, query parameters, or fragments. Its path and descendants are authorized; `/v1` matches `/v1/chat` but not `/v10`.

## Authorization and quotas

Envoy calls `/authz/check` with the original protected path appended. A valid token is accepted only when its project is active, has a limit class, and the request is below the configured API prefix.

Successful checks return:

- `x-current-user`
- `x-current-project`
- `x-current-project-id`
- `x-current-quota-key`
- `x-current-quota-class`

Configure Envoy to forward these authorization-response headers. Daily token quotas use the opaque project quota key. Token usage comes from AI Gateway metadata `io.envoy.ai_gateway/llm_total_token` delivered through ALS.

Concurrency limits become `BackendTrafficPolicy` circuit breakers with `maxPendingRequests: 0`, so overflow fails fast. Circuit-breaker counters are maintained separately by each Envoy pod. Policy changes take effect after a superuser selects **Deploy all Envoy policies** on the Django admin start page.

## Kubernetes deployment

The production Kustomize overlay deploys two GrantDeck pods and one PostgreSQL StatefulSet. It expects an existing namespace, Gateway API CRDs, and a Gateway that accepts routes from the GrantDeck namespace.

```bash
cp deploy/k8s/overlays/production/secret.env.example \
  deploy/k8s/overlays/production/secret.env
kubectl kustomize deploy/k8s/overlays/production
make deploy
```

Set deployment-specific values in `deploy/k8s/overlays/production/config.env` and secrets in the gitignored `secret.env`. Startup migrations are serialized with a PostgreSQL advisory lock. The policy deployer service account is limited to the generated Envoy Gateway resource types.

The Envoy Gateway configuration must send ALS data to `grantdeck:8001` and include the GrantDeck identity headers and AI Gateway token metadata. The supplied NetworkPolicy restricts this port to the Envoy namespace.

## Development

| Target | Effect |
| --- | --- |
| `make venv` | Creates `.venv` and installs the application in editable mode |
| `make run` | Loads `.env` when present and starts Django's development server |
| `make test` | Installs development dependencies and runs the pytest suite |
| `make generate_als_proto` | Regenerates Python gRPC bindings from `proto/usage_als.proto` |
| `make deploy` | Applies the production Kustomize overlay with `kubectl apply -k` |
| `make release VERSION=X.Y.Z` | Creates and pushes an annotated semantic-version Git tag |

For local development, put `GDK_ENV=development` in `.env`, then run:

```bash
make run
make test
```

Create a local administrator with `manage.py createsuperuser` and use `/login/admin`. Build the production image with:

```bash
docker build -f deploy/Dockerfile -t grantdeck:local .
```

Use `make generate_als_proto` after changing the ALS protocol definition. The release target requires a numeric `MAJOR.MINOR.PATCH` value and pushes `vMAJOR.MINOR.PATCH` to the configured Git remote.

## Architecture principles

- Keep the data plane in Envoy and the control plane in GrantDeck.
- Prefer standard Django features and small, direct implementations.
- Add dependencies only when they remove more complexity than they introduce.
- Keep data-related behavior in models or managers and views thin.
- Keep self-service functions in the frontend and administrative functions in Django admin.
- Make production the safe default; enable development mode explicitly.
- Store secrets only as hashes and require bearer tokens to expire.
- Log authorization decisions without exposing credentials or sensitive headers.
- Prefer clear ORM usage over custom prefetch abstractions.
- Test functional behavior, permissions, and security decisions with pytest; keep UI tests focused.
- Use consistent user-facing terminology, including **Bearer token**.
