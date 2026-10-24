from django.conf import settings
from django.core.management.base import CommandError

from apps.authz.models import Project
from apps.authz.quota_keys import exported_route_name


MANAGED_BY_LABEL = 'app.kubernetes.io/managed-by'
MANAGED_BY_VALUE = 'grantdeck-export'


def managed_metadata(name):
    return {
        'name': name,
        'labels': {MANAGED_BY_LABEL: MANAGED_BY_VALUE},
    }


def route_document(project, model_limit):
    backend = settings.AIGATEWAY_MODEL_BACKENDS.get(model_limit.model_name)
    if backend is None:
        raise CommandError(
            f'No AIGateway backend mapping exists for model {model_limit.model_name!r}.'
        )

    name = exported_route_name(project, model_limit.model_name)
    return {
        'apiVersion': 'aigateway.envoyproxy.io/v1beta1',
        'kind': 'AIGatewayRoute',
        'metadata': managed_metadata(name),
        'spec': {
            'parentRefs': [{
                'group': 'gateway.networking.k8s.io',
                'kind': 'Gateway',
                'name': settings.AIGATEWAY_GATEWAY_NAME,
                'namespace': settings.AIGATEWAY_GATEWAY_NAMESPACE,
                'sectionName': settings.AIGATEWAY_GATEWAY_SECTION,
            }, {
                'group': 'gateway.networking.k8s.io',
                'kind': 'Gateway',
                'name': settings.AIGATEWAY_GATEWAY_NAME,
                'namespace': settings.AIGATEWAY_GATEWAY_NAMESPACE,
                'sectionName': settings.AIGATEWAY_GATEWAY_INTERNAL_SECTION,
            }],
            'llmRequestCosts': [{'metadataKey': 'llm_total_token', 'type': 'TotalToken'}],
            'rules': [{
                'matches': [{
                    'headers': [
                        {
                            'name': 'x-current-project-id',
                            'type': 'Exact',
                            'value': str(project.pk),
                        },
                        {
                            'name': 'x-ai-eg-model',
                            'type': 'Exact',
                            'value': model_limit.model_name,
                        },
                    ],
                }],
                'modelsOwnedBy': settings.AIGATEWAY_MODELS_OWNER,
                'timeouts': {'request': '30m'},
                'backendRefs': [backend],
            }],
        },
    }


def quota_document(project, model_limit):
    target_name = exported_route_name(project, model_limit.model_name)
    document = {
        'apiVersion': 'gateway.envoyproxy.io/v1alpha1',
        'kind': 'BackendTrafficPolicy',
        'metadata': managed_metadata(f'{target_name}-quota'),
        'spec': {
            'targetRefs': [{
                'group': 'gateway.networking.k8s.io',
                'kind': 'HTTPRoute',
                'name': target_name,
            }],
            'rateLimit': {
                'type': 'Global',
                'global': {
                    'rules': [{
                        'clientSelectors': [{
                            'headers': [{
                                'name': 'x-current-quota-key',
                                'type': 'Distinct',
                            }],
                        }],
                        'limit': {
                            'requests': model_limit.daily_token_limit,
                            'unit': 'Day',
                        },
                        'cost': {
                            'request': {'from': 'Number', 'number': 0},
                            'response': {
                                'from': 'Metadata',
                                'metadata': {
                                    'namespace': 'io.envoy.ai_gateway',
                                    'key': 'llm_total_token',
                                },
                            },
                        },
                    }],
                },
            },
        },
    }
    if model_limit.max_parallel_requests is not None:
        document['spec']['circuitBreaker'] = {
            'maxParallelRequests': model_limit.max_parallel_requests,
            # Envoy counts requests waiting for a new upstream connection as
            # pending. A zero limit can therefore deadlock a cold or rebuilt
            # cluster before its first connection is established.
            'maxPendingRequests': model_limit.max_parallel_requests,
        }
    return document


def security_policy_document(route_names):
    return {
        'apiVersion': 'gateway.envoyproxy.io/v1alpha1',
        'kind': 'SecurityPolicy',
        'metadata': managed_metadata('inference-grantdeck-class-auth'),
        'spec': {
            'targetRefs': [
                {
                    'group': 'gateway.networking.k8s.io',
                    'kind': 'HTTPRoute',
                    'name': name,
                }
                for name in route_names
            ],
            'extAuth': {
                'failOpen': False,
                'recomputeRoute': True,
                'headersToExtAuth': ['Authorization', 'X-Forwarded-Proto', 'X-Forwarded-Host'],
                'http': {
                    'backendRefs': [{'name': 'grantdeck', 'port': 8000}],
                    'path': '/authz/check',
                    'headersToBackend': [
                        'x-current-user',
                        'x-current-project',
                        'x-current-project-id',
                        'x-current-quota-key',
                        'x-current-quota-class',
                    ],
                },
            },
        },
    }


def build_policy_documents():
    projects = list(
        Project.objects.filter(limit_class__isnull=False).order_by('pk')
        .select_related('limit_class').prefetch_related('limit_class__model_limits')
    )
    documents = []
    route_names = []
    for project in projects:
        for model_limit in project.limit_class.model_limits.all():
            route = route_document(project, model_limit)
            documents.extend([route, quota_document(project, model_limit)])
            route_names.append(route['metadata']['name'])

    if route_names:
        documents.append(security_policy_document(route_names))
    return documents
