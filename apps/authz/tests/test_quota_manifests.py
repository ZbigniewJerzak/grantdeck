import pytest
from django.core.management.base import CommandError
from django.test import override_settings

from apps.authz.quota_keys import exported_route_name
from apps.authz.models import Project, ProjectLimitClass, ProjectModelLimit
from apps.authz.policies import build_policy_documents


pytestmark = pytest.mark.django_db


EXPORTER_SETTINGS = {
    'AIGATEWAY_GATEWAY_NAME': 'test-gateway',
    'AIGATEWAY_GATEWAY_NAMESPACE': 'test-gateway-system',
    'AIGATEWAY_GATEWAY_SECTION': 'test-api',
    'AIGATEWAY_GATEWAY_INTERNAL_SECTION': 'test-internal',
    'AIGATEWAY_MODELS_OWNER': 'test-owner',
    'AIGATEWAY_MODEL_BACKENDS': {'bht/large': {'name': 'test-backend'}},
}


@override_settings(**EXPORTER_SETTINGS)
def test_export_isolates_projects_sharing_a_limit_class():
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
        max_parallel_requests=2,
    )
    projects = [
        Project.objects.create(name='First', shortcut='first', limit_class=policy_class),
        Project.objects.create(name='Second', shortcut='second', limit_class=policy_class),
    ]
    Project.objects.create(name='Unassigned', shortcut='unassigned')

    documents = build_policy_documents()

    routes = [document for document in documents if document['kind'] == 'AIGatewayRoute']
    quotas = [document for document in documents if document['kind'] == 'BackendTrafficPolicy']
    security = next(document for document in documents if document['kind'] == 'SecurityPolicy')
    assert len(routes) == len(quotas) == 2
    for project in projects:
        route_ref = exported_route_name(project, 'bht/large')
        route = next(route for route in routes if route['metadata']['name'] == route_ref)
        assert route['spec']['rules'][0]['matches'][0]['headers'] == [
            {'name': 'x-current-project-id', 'type': 'Exact', 'value': str(project.pk)},
            {'name': 'x-ai-eg-model', 'type': 'Exact', 'value': 'bht/large'},
        ]
        quota = next(quota for quota in quotas if quota['spec']['targetRefs'][0]['name'] == route_ref)
        assert quota['spec']['circuitBreaker']['maxParallelRequests'] == 2
    assert {target['name'] for target in security['spec']['targetRefs']} == {
        route['metadata']['name'] for route in routes
    }


@override_settings(**EXPORTER_SETTINGS)
def test_export_generates_project_model_routes_quota_and_auth_resources():
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    project = Project.objects.create(name='Project', shortcut='project', limit_class=policy_class)
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
        max_parallel_requests=16,
    )

    documents = build_policy_documents()

    route = next(document for document in documents if document['kind'] == 'AIGatewayRoute')
    quota = next(document for document in documents if document['kind'] == 'BackendTrafficPolicy')
    security = next(document for document in documents if document['kind'] == 'SecurityPolicy')
    route_ref = exported_route_name(project, 'bht/large')

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
        {'name': 'x-current-project-id', 'type': 'Exact', 'value': str(project.pk)},
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
        'maxPendingRequests': 16,
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
    Project.objects.create(name='Project', shortcut='project', limit_class=policy_class)
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='unknown/model',
        daily_token_limit=10_000,
    )

    with pytest.raises(CommandError, match='No AIGateway backend mapping'):
        build_policy_documents()


@override_settings(**EXPORTER_SETTINGS)
def test_export_omits_circuit_breaker_when_limits_are_unset():
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    Project.objects.create(name='Project', shortcut='project', limit_class=policy_class)
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=250_000,
    )

    quota = next(
        document
        for document in build_policy_documents()
        if document['kind'] == 'BackendTrafficPolicy'
    )

    assert 'circuitBreaker' not in quota['spec']
