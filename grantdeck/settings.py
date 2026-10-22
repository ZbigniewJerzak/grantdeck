import json
import os
from pathlib import Path

import dj_database_url
from django.contrib.messages import constants as message_constants
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


GDK_ENV = os.environ.get('GDK_ENV', 'production')
IS_DEVELOPMENT = GDK_ENV == 'development'
REQUIRE_PRODUCTION_ENV = not IS_DEVELOPMENT


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f'{name} must be set')
    return value


def env_bool(name, default=False):
    return env(name, str(default)).lower() in {'1', 'true', 'yes', 'on'}


def env_csv(name, default='', required=False):
    return [item.strip() for item in env(name, default, required=required).split(',') if item.strip()]


def env_json(name, default=None, required=False):
    value = env(name, default, required=required)
    if value in (None, ''):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(f'{name} must contain valid JSON') from exc
    if not isinstance(parsed, dict):
        raise ImproperlyConfigured(f'{name} must contain a JSON object')
    return parsed


def aigateway_model_backends():
    backends = env_json('GDK_AIGATEWAY_MODEL_BACKENDS', required=REQUIRE_PRODUCTION_ENV)
    for model_name, backend in backends.items():
        if not isinstance(model_name, str) or not model_name.strip():
            raise ImproperlyConfigured(
                'GDK_AIGATEWAY_MODEL_BACKENDS keys must be non-empty model names'
            )
        if not isinstance(backend, dict) or not backend.get('name'):
            raise ImproperlyConfigured(
                f'GDK_AIGATEWAY_MODEL_BACKENDS[{model_name!r}] must contain a backend name'
            )
    return backends


def database_config():
    database_url = env(
        'GDK_DATABASE_URL',
        f'sqlite:///{BASE_DIR / "db.sqlite3"}' if IS_DEVELOPMENT else None,
        required=REQUIRE_PRODUCTION_ENV,
    )
    return {
        'default': dj_database_url.config(
            env='GDK_DATABASE_URL',
            default=database_url,
        ),
    }


SECRET_KEY = env(
    'GDK_SECRET_KEY',
    'dev-only-insecure-secret-key-change-me' if IS_DEVELOPMENT else None,
    required=REQUIRE_PRODUCTION_ENV,
)

DEBUG = IS_DEVELOPMENT

ALLOWED_HOSTS = env_csv('GDK_ALLOWED_HOSTS', required=REQUIRE_PRODUCTION_ENV)
CSRF_TRUSTED_ORIGINS = env_csv('GDK_CSRF_TRUSTED_ORIGINS')
FORCE_SCRIPT_NAME = env('GDK_SCRIPT_NAME') or None
AUTHZ_CANONICAL_ORIGIN = env(
    'GDK_AUTHZ_CANONICAL_ORIGIN',
    '' if IS_DEVELOPMENT else None,
    required=REQUIRE_PRODUCTION_ENV,
).rstrip('/')

BRAND_NAME = env('GDK_BRAND_NAME', 'GrantDeck')
SSO_LOGIN_BUTTON_TEXT = env('GDK_SSO_LOGIN_BUTTON_TEXT', 'Login with SSO')
STATIC_TOKEN_MAX_LIFETIME_DAYS = int(env('GDK_STATIC_TOKEN_MAX_LIFETIME_DAYS', '183'))
STATIC_TOKEN_DEFAULT_LIFETIME_DAYS = min(183, STATIC_TOKEN_MAX_LIFETIME_DAYS)
PROJECT_JOIN_ATTEMPT_LIMIT = int(env('GDK_PROJECT_JOIN_ATTEMPT_LIMIT', '5'))
PROJECT_JOIN_ATTEMPT_WINDOW_SECONDS = int(env('GDK_PROJECT_JOIN_ATTEMPT_WINDOW_SECONDS', '600'))
USAGE_METRICS_TOKEN = env(
    'GDK_USAGE_METRICS_TOKEN',
    'dev-only-metrics-token' if IS_DEVELOPMENT else None,
)

INSTALLED_APPS = [
    'django.contrib.admin',
    'grantdeck.apps.AuthConfig',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_bootstrap5',
    'social_django',
    'apps.authz.apps.AuthzConfig',
    'apps.frontend.apps.FrontendConfig',
    'apps.usage.apps.UsageConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'grantdeck.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'social_django.context_processors.backends',
                'social_django.context_processors.login_redirect',
                'apps.frontend.context_processors.branding',
                'apps.frontend.context_processors.navigation',
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    'social_core.backends.open_id_connect.OpenIdConnectAuth',
    'django.contrib.auth.backends.ModelBackend',
]

SOCIAL_AUTH_URL_NAMESPACE = 'auth'
SOCIAL_AUTH_OIDC_OIDC_ENDPOINT = env('GDK_OIDC_ENDPOINT', '' if IS_DEVELOPMENT else None, required=REQUIRE_PRODUCTION_ENV)
SOCIAL_AUTH_OIDC_KEY = env('GDK_OIDC_CLIENT_ID', '' if IS_DEVELOPMENT else None, required=REQUIRE_PRODUCTION_ENV)
SOCIAL_AUTH_OIDC_SECRET = env('GDK_OIDC_CLIENT_SECRET', '' if IS_DEVELOPMENT else None, required=REQUIRE_PRODUCTION_ENV)
GDK_OIDC_ADMIN_USERNAME = env('GDK_OIDC_ADMIN_USERNAME', '')
SOCIAL_AUTH_OIDC_TOKEN_ENDPOINT_AUTH_METHOD = 'client_secret_post'
SOCIAL_AUTH_PIPELINE = (
    'social_core.pipeline.social_auth.social_details',
    'social_core.pipeline.social_auth.social_uid',
    'apps.frontend.pipeline.log_oauth_response',
    'social_core.pipeline.social_auth.auth_allowed',
    'social_core.pipeline.social_auth.social_user',
    'social_core.pipeline.user.get_username',
    'social_core.pipeline.user.create_user',
    'social_core.pipeline.social_auth.associate_user',
    'social_core.pipeline.social_auth.load_extra_data',
    'social_core.pipeline.user.user_details',
    'apps.frontend.pipeline.promote_oidc_admin',
)

LOGIN_URL = f'{FORCE_SCRIPT_NAME or ""}/login/'
LOGIN_REDIRECT_URL = f'{FORCE_SCRIPT_NAME or ""}/'
LOGOUT_REDIRECT_URL = f'{FORCE_SCRIPT_NAME or ""}/login/'
MESSAGE_TAGS = {message_constants.ERROR: 'danger'}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
SESSION_COOKIE_SECURE = env_bool('GDK_SESSION_COOKIE_SECURE')
CSRF_COOKIE_SECURE = env_bool('GDK_CSRF_COOKIE_SECURE')
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = env_bool('GDK_CSRF_COOKIE_HTTPONLY')
GDK_LOG_LEVEL = env('GDK_LOG_LEVEL', 'INFO').upper()

# These values describe the cluster-side AIGateway deployment used by the
# manifest exporter. They are deliberately not given application defaults.
AIGATEWAY_GATEWAY_NAME = env('GDK_AIGATEWAY_GATEWAY_NAME', required=REQUIRE_PRODUCTION_ENV)
AIGATEWAY_GATEWAY_NAMESPACE = env(
    'GDK_AIGATEWAY_GATEWAY_NAMESPACE', required=REQUIRE_PRODUCTION_ENV
)
AIGATEWAY_GATEWAY_SECTION = env(
    'GDK_AIGATEWAY_GATEWAY_SECTION', required=REQUIRE_PRODUCTION_ENV
)
AIGATEWAY_GATEWAY_INTERNAL_SECTION = env(
    'GDK_AIGATEWAY_GATEWAY_INTERNAL_SECTION', required=REQUIRE_PRODUCTION_ENV
)
AIGATEWAY_MODELS_OWNER = env('GDK_AIGATEWAY_MODELS_OWNER', required=REQUIRE_PRODUCTION_ENV)
AIGATEWAY_MODEL_BACKENDS = aigateway_model_backends()

WSGI_APPLICATION = 'grantdeck.wsgi.application'

DATABASES = database_config()

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Europe/Berlin'

USE_I18N = True

USE_TZ = True

STATIC_URL = f'{FORCE_SCRIPT_NAME or ""}/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'formatters': {
        'simple': {
            'format': '%(levelname)s %(name)s %(message)s',
        },
    },
    'loggers': {
        'apps': {
            'handlers': ['console'],
            'level': GDK_LOG_LEVEL,
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': GDK_LOG_LEVEL,
            'propagate': False,
        },
    },
}
