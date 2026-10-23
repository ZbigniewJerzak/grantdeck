import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.test import Client
from social_core.backends.open_id_connect import OpenIdConnectAuth
from social_django.strategy import DjangoStrategy

from grantdeck import settings


GDK_ENV_VARS = [
    'GDK_ENV',
    'GDK_SECRET_KEY',
    'GDK_DATABASE_URL',
    'GDK_ALLOWED_HOSTS',
    'GDK_AUTHZ_API_ENDPOINT',
    'GDK_OIDC_ENDPOINT',
    'GDK_OIDC_CLIENT_ID',
    'GDK_OIDC_CLIENT_SECRET',
    'GDK_STATIC_TOKEN_MAX_LIFETIME_DAYS',
    'GDK_SCRIPT_NAME',
    'GDK_LOG_LEVEL',
    'GDK_AIGATEWAY_GATEWAY_NAME',
    'GDK_AIGATEWAY_GATEWAY_NAMESPACE',
    'GDK_AIGATEWAY_GATEWAY_SECTION',
    'GDK_AIGATEWAY_GATEWAY_INTERNAL_SECTION',
    'GDK_AIGATEWAY_MODELS_OWNER',
    'GDK_AIGATEWAY_MODEL_BACKENDS',
]

PRODUCTION_ENV = {
    'GDK_SECRET_KEY': 'production-secret',
    'GDK_DATABASE_URL': 'postgres://user:pass@db.example:5432/grantdeck',
    'GDK_ALLOWED_HOSTS': 'grantdeck.example.test,grantdeck.internal',
    'GDK_AUTHZ_API_ENDPOINT': 'https://api.example.test/v1/',
    'GDK_OIDC_ENDPOINT': 'https://issuer.example.test',
    'GDK_OIDC_CLIENT_ID': 'client-id',
    'GDK_OIDC_CLIENT_SECRET': 'client-secret',
    'GDK_AIGATEWAY_GATEWAY_NAME': 'test-gateway',
    'GDK_AIGATEWAY_GATEWAY_NAMESPACE': 'test-gateway-system',
    'GDK_AIGATEWAY_GATEWAY_SECTION': 'test-api',
    'GDK_AIGATEWAY_GATEWAY_INTERNAL_SECTION': 'test-internal',
    'GDK_AIGATEWAY_MODELS_OWNER': 'test-owner',
    'GDK_AIGATEWAY_MODEL_BACKENDS': '{"bht/large":{"name":"test-backend"}}',
}


def reload_settings(monkeypatch, **env):
    for name in GDK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(settings)


@pytest.mark.parametrize('name', PRODUCTION_ENV)
def test_production_requires_environment(monkeypatch, name):
    env = PRODUCTION_ENV | {name: None}
    env = {key: value for key, value in env.items() if value is not None}

    with pytest.raises(ImproperlyConfigured, match=name):
        reload_settings(monkeypatch, **env)


@pytest.mark.parametrize('name', PRODUCTION_ENV)
def test_production_rejects_blank_required_values(monkeypatch, name):
    env = PRODUCTION_ENV | {name: ''}

    with pytest.raises(ImproperlyConfigured, match=name):
        reload_settings(monkeypatch, **env)


def test_production_settings_load_with_required_environment(monkeypatch):
    loaded = reload_settings(
        monkeypatch,
        **PRODUCTION_ENV,
        GDK_DEBUG='true',
    )

    assert loaded.DEBUG is False
    assert loaded.SECRET_KEY == 'production-secret'
    assert loaded.ALLOWED_HOSTS == ['grantdeck.example.test', 'grantdeck.internal']
    assert loaded.AUTHZ_API_ENDPOINT == 'https://api.example.test/v1'
    assert loaded.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'
    assert loaded.SOCIAL_AUTH_OIDC_KEY == 'client-id'
    assert loaded.SOCIAL_AUTH_OIDC_TOKEN_ENDPOINT_AUTH_METHOD == 'client_secret_post'
    assert loaded.GDK_LOG_LEVEL == 'INFO'
    assert loaded.LOGGING['loggers']['django.request']['level'] == 'INFO'
    backend = OpenIdConnectAuth(strategy=DjangoStrategy(storage=None))
    assert backend.use_basic_auth() is False
    assert loaded.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')
    assert loaded.USE_X_FORWARDED_HOST is True
    assert loaded.STATIC_TOKEN_MAX_LIFETIME_DAYS == 183
    assert loaded.STATIC_TOKEN_DEFAULT_LIFETIME_DAYS == 183


def test_development_settings_keep_local_defaults(monkeypatch):
    loaded = reload_settings(monkeypatch, GDK_ENV='development', GDK_DEBUG='false')

    assert loaded.DEBUG is True
    assert loaded.SECRET_KEY == 'dev-only-insecure-secret-key-change-me'
    assert loaded.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
    assert loaded.DATABASES['default']['NAME'] == str(loaded.BASE_DIR / 'db.sqlite3')
    assert loaded.FORCE_SCRIPT_NAME is None
    assert loaded.AUTHZ_API_ENDPOINT == 'http://testserver/'


@pytest.mark.parametrize('endpoint', [
    'api.example.test/v1',
    'ftp://api.example.test/v1',
    'https://user:secret@api.example.test/v1',
    'https://api.example.test/v1?key=value',
    'https://api.example.test/v1#fragment',
])
def test_api_endpoint_must_be_a_plain_absolute_http_url(monkeypatch, endpoint):
    with pytest.raises(ImproperlyConfigured, match='GDK_AUTHZ_API_ENDPOINT'):
        reload_settings(
            monkeypatch,
            **(PRODUCTION_ENV | {'GDK_AUTHZ_API_ENDPOINT': endpoint}),
        )


def test_log_level_can_enable_request_debug_logs(monkeypatch):
    loaded = reload_settings(
        monkeypatch,
        **PRODUCTION_ENV,
        GDK_LOG_LEVEL='debug',
    )

    assert loaded.GDK_LOG_LEVEL == 'DEBUG'
    assert loaded.LOGGING['loggers']['apps']['level'] == 'DEBUG'
    assert loaded.LOGGING['loggers']['django.request']['level'] == 'DEBUG'


def test_script_name_can_be_configured(monkeypatch):
    loaded = reload_settings(
        monkeypatch,
        **PRODUCTION_ENV,
        GDK_SCRIPT_NAME='/grantdeck',
    )

    assert loaded.FORCE_SCRIPT_NAME == '/grantdeck'
    assert loaded.STATIC_URL == '/grantdeck/static/'


@override_settings(FORCE_SCRIPT_NAME='/grantdeck', LOGIN_URL='/grantdeck/login/')
def test_reversed_urls_include_script_name():
    response = Client().get('/')

    assert response.status_code == 302
    assert response.headers['Location'] == '/grantdeck/login/?next=/grantdeck/'


def test_static_token_max_lifetime_can_be_configured(monkeypatch):
    loaded = reload_settings(
        monkeypatch,
        **PRODUCTION_ENV,
        GDK_STATIC_TOKEN_MAX_LIFETIME_DAYS='30',
    )

    assert loaded.STATIC_TOKEN_MAX_LIFETIME_DAYS == 30
    assert loaded.STATIC_TOKEN_DEFAULT_LIFETIME_DAYS == 30


def test_database_config_reads_gdk_database_url_in_development(monkeypatch):
    loaded = reload_settings(
        monkeypatch,
        GDK_ENV='development',
        GDK_DATABASE_URL='postgres://user:pass@db.example:5432/grantdeck',
    )

    database = loaded.database_config()['default']

    assert database['ENGINE'] == 'django.db.backends.postgresql'
    assert database['NAME'] == 'grantdeck'
    assert database['USER'] == 'user'
    assert database['PASSWORD'] == 'pass'
    assert database['HOST'] == 'db.example'
    assert database['PORT'] == 5432


def test_aigateway_backend_mapping_requires_named_backend(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match='backend name'):
        reload_settings(monkeypatch, **(
            PRODUCTION_ENV
            | {'GDK_AIGATEWAY_MODEL_BACKENDS': '{"bht/large": {}}'}
        ))


def test_aigateway_backend_mapping_rejects_non_object_json(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match='JSON object'):
        reload_settings(monkeypatch, **(
            PRODUCTION_ENV
            | {'GDK_AIGATEWAY_MODEL_BACKENDS': '[]'}
        ))
