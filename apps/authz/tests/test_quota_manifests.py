import io
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.authz.quota_keys import exported_route_name
from apps.authz.models import ProjectLimitClass, ProjectModelLimit


pytestmark = pytest.mark.django_db


EXPORTER_SETTINGS = {
    'AIGATEWAY_GATEWAY_NAME': 'test-gateway',
    'AIGATEWAY_GATEWAY_NAMESPACE': 'test-gateway-system',
    'AIGATEWAY_GATEWAY_SECTION': 'test-api',
    'AIGATEWAY_GATEWAY_INTERNAL_SECTION': 'test-internal',
    'AIGATEWAY_MODELS_OWNER': 'test-owner',
    'AIGATEWAY_MODEL_BACKENDS': {'bht/large': {'name': 'test-backend'}},
}


def export_documents():
    output = io.StringIO()
    call_command('export_quota_manifests', stdout=output)
    return [json.loads(document) for document in output.getvalue().split('---') if document.strip()]


@override_settings(**EXPORTER_SETTINGS)
def test_export_generates_class_model_routes_quota_and_auth_resources():
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
        max_parallel_requests=16,
        max_pending_requests=32,
    )

    documents = export_documents()

    route = next(document for document in documents if document['kind'] == 'AIGatewayRoute')
    quota = next(document for document in documents if document['kind'] == 'BackendTrafficPolicy')
    security = next(document for document in documents if document['kind'] == 'SecurityPolicy')
    route_ref = exported_route_name(policy_class, 'bht/large')

    assert route['metadata']['name'] == route_ref
    assert route['spec']['parentRefs'] == [
        {
            'group': 'gateway.networking.k8s.io',
            'kind': 'Gateway',
            'name': 'test-gateway',
            'namespace': 'test-gateway-system',
            'sectionName': 'test-api',
        },
        {
            'group': 'gateway.networking.k8s.io',
            'kind': 'Gateway',
            'name': 'test-gateway',
            'namespace': 'test-gateway-system',
            'sectionName': 'test-internal',
        },
    ]
    headers = route['spec']['rules'][0]['matches'][0]['headers']
    assert headers == [
        {'name': 'x-current-quota-class', 'type': 'Exact', 'value': 'research'},
        {'name': 'x-ai-eg-model', 'type': 'Exact', 'value': 'bht/large'},
    ]
    assert quota['spec']['targetRefs'][0]['name'] == route_ref
    rule = quota['spec']['rateLimit']['global']['rules'][0]
    assert rule['clientSelectors'][0]['headers'] == [
        {'name': 'x-current-quota-key', 'type': 'Distinct'},
    ]
    assert rule['limit'] == {'requests': 250_000, 'unit': 'Day'}
    assert rule['cost']['response']['metadata']['key'] == 'llm_total_token'
    assert quota['spec']['circuitBreaker'] == {
        'maxParallelRequests': 16,
        'maxPendingRequests': 32,
    }
    assert security['spec']['targetRefs'] == [{
        'group': 'gateway.networking.k8s.io',
        'kind': 'HTTPRoute',
        'name': route_ref,
    }]
    assert 'x-current-quota-class' in security['spec']['extAuth']['http']['headersToBackend']
    assert 'x-current-quota-key' in security['spec']['extAuth']['http']['headersToBackend']
    assert 'x-current-project-id' in security['spec']['extAuth']['http']['headersToBackend']
    assert 'x-ai-eg-model' not in security['spec']['extAuth']['headersToExtAuth']
    assert security['spec']['extAuth']['recomputeRoute'] is True


@override_settings(**EXPORTER_SETTINGS)
def test_export_fails_for_model_without_configured_backend_mapping():
    policy_class = ProjectLimitClass.objects.create(name='Unsupported', slug='unsupported')
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='unknown/model',
        daily_token_limit=10_000,
    )

    with pytest.raises(CommandError, match='No AIGateway backend mapping'):
        export_documents()


@override_settings(**EXPORTER_SETTINGS)
def test_export_omits_circuit_breaker_when_limits_are_unset():
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
    )

    quota = next(document for document in export_documents() if document['kind'] == 'BackendTrafficPolicy')

    assert 'circuitBreaker' not in quota['spec']


@override_settings(**EXPORTER_SETTINGS)
def test_export_writes_one_file_per_resource(tmp_path):
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
    )

    output_dir = tmp_path / 'quota'
    call_command('export_quota_manifests', output_dir=str(output_dir))

    files = sorted(path for path in output_dir.iterdir() if path.suffix == '.yml')
    assert len(files) == 3
    assert {json.loads(path.read_text())['kind'] for path in files} == {
        'AIGatewayRoute', 'BackendTrafficPolicy', 'SecurityPolicy',
    }
    assert (output_dir / '.grantdeck-export').exists()

    call_command('export_quota_manifests', output_dir=str(output_dir))
    assert len(list(output_dir.glob('*.yml'))) == 3


@override_settings(**EXPORTER_SETTINGS)
def test_export_refuses_nonempty_output_directory(tmp_path):
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
    )
    output_dir = tmp_path / 'quota'
    output_dir.mkdir()
    (output_dir / 'unmanaged.yml').write_text('{}')

    with pytest.raises(CommandError, match='must be empty'):
        call_command('export_quota_manifests', output_dir=str(output_dir))
