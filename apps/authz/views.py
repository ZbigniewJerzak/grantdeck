import logging
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from apps.authz.models import TOKEN_PREFIX, StaticToken


logger = logging.getLogger(__name__)

_SENSITIVE_HEADER_NAMES = frozenset({
    'authorization',
    'proxy-authorization',
    'cookie',
    'set-cookie',
    'x-api-key',
    'x-auth-token',
    'x-csrf-token',
    'x-current-user',
    'x-current-project',
    'x-current-project-id',
    'x-current-quota-key',
    'x-current-quota-class',
})


def _log_incoming_headers(request):
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _SENSITIVE_HEADER_NAMES
    }
    logger.debug('incoming headers=%s', dict(sorted(headers.items())))


def _log_authz(request, status, result, user='', protected_url='', model=''):
    logger.info(
        'authz check user=%s result=%s status=%s resource=%s model=%s',
        user or '-',
        result,
        status,
        protected_url or '-',
        model or '-',
        extra={
            'authz_user': user,
            'authz_result': result,
            'authz_status': status,
            'authz_method': request.method,
            'authz_path': request.path,
            'authz_resource_url': protected_url,
            'authz_model': model,
        },
    )


def _unauthorized_response():
    response = HttpResponse(status=401)
    response['WWW-Authenticate'] = 'Bearer'
    return response


def _protected_url(protected_path):
    path = f'/{protected_path}' if protected_path else '/'
    endpoint = urlsplit(settings.AUTHZ_API_ENDPOINT)
    return urlunsplit((endpoint.scheme, endpoint.netloc, path, '', ''))


def _matches_api_endpoint(url):
    endpoint = urlsplit(settings.AUTHZ_API_ENDPOINT)
    candidate = urlsplit(url)
    if (candidate.scheme, candidate.netloc) != (endpoint.scheme, endpoint.netloc):
        return False
    endpoint_path = endpoint.path.rstrip('/') or '/'
    candidate_path = candidate.path.rstrip('/') or '/'
    return (
        endpoint_path == '/'
        or candidate_path == endpoint_path
        or candidate_path.startswith(f'{endpoint_path}/')
    )


@csrf_exempt
def envoy_authz_check(request, protected_path=''):
    _log_incoming_headers(request)
    parts = request.headers.get('Authorization', '').split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        _log_authz(request, 401, 'unauthorized')
        return _unauthorized_response()

    raw_token = parts[1]
    if not raw_token.startswith(TOKEN_PREFIX):
        _log_authz(request, 401, 'unauthorized')
        return _unauthorized_response()

    token = StaticToken.find_valid(raw_token)
    if token is None:
        _log_authz(request, 403, 'forbidden')
        return HttpResponse(status=403)

    username = token.user.get_username()
    protected_url = _protected_url(protected_path)
    if token.project.limit_class_id is None or not _matches_api_endpoint(protected_url):
        token.record_denied()
        _log_authz(request, 403, 'forbidden', username, protected_url=protected_url)
        return HttpResponse(status=403)

    token.record_allowed()
    _log_authz(request, 200, 'allowed', username, protected_url)
    response = HttpResponse(status=200)
    response['x-current-user'] = username
    response['x-current-project'] = token.project.shortcut
    response['x-current-project-id'] = str(token.project.pk)
    response['x-current-quota-key'] = token.project.quota_key
    response['x-current-quota-class'] = token.project.limit_class.slug
    response['x-envoy-auth-headers-to-remove'] = 'authorization'
    return response
