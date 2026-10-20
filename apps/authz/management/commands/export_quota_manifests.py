import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.authz.models import ProjectLimitClass
from apps.authz.quota_keys import exported_route_name


def route_document(policy_class, model_limit):
    backend = settings.AIGATEWAY_MODEL_BACKENDS.get(model_limit.model_name)
    if backend is None:
        raise CommandError(
            f'No AIGateway backend mapping exists for model {model_limit.model_name!r}.'
        )

    name = exported_route_name(policy_class, model_limit.model_name)
    return {
        'apiVersion': 'aigateway.envoyproxy.io/v1beta1',
        'kind': 'AIGatewayRoute',
        'metadata': {
            'name': name,
            'labels': {'app.kubernetes.io/managed-by': 'grantdeck-export'},
        },
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
                            'name': 'x-current-quota-class',
                            'type': 'Exact',
                            'value': policy_class.slug,
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


def quota_document(policy_class, model_limit):
    target_name = exported_route_name(policy_class, model_limit.model_name)
    document = {
        'apiVersion': 'gateway.envoyproxy.io/v1alpha1',
        'kind': 'BackendTrafficPolicy',
        'metadata': {
            'name': f'{target_name}-quota',
            'labels': {'app.kubernetes.io/managed-by': 'grantdeck-export'},
        },
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
    circuit_breaker = {
        field: value
        for field, value in (
            ('maxParallelRequests', model_limit.max_parallel_requests),
            ('maxPendingRequests', model_limit.max_pending_requests),
        )
        if value is not None
    }
    if circuit_breaker:
        document['spec']['circuitBreaker'] = circuit_breaker
    return document


def security_policy_document(route_names):
    return {
        'apiVersion': 'gateway.envoyproxy.io/v1alpha1',
        'kind': 'SecurityPolicy',
        'metadata': {
            'name': 'inference-grantdeck-class-auth',
            'labels': {'app.kubernetes.io/managed-by': 'grantdeck-export'},
        },
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


class Command(BaseCommand):
    help = 'Export class/model AIGateway resources as separate YAML-compatible JSON files.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            type=Path,
            help='Write one resource file per Kubernetes object to this directory.',
        )

    def export_documents(self):
        policy_classes = list(
            ProjectLimitClass.objects.order_by('pk').prefetch_related('model_limits')
        )
        documents = []
        route_names = []
        for policy_class in policy_classes:
            for model_limit in policy_class.model_limits.all():
                route = route_document(policy_class, model_limit)
                documents.extend([route, quota_document(policy_class, model_limit)])
                route_names.append(route['metadata']['name'])

        if route_names:
            documents.append(security_policy_document(route_names))
        return documents

    def write_directory(self, output_dir, documents):
        output_dir.mkdir(parents=True, exist_ok=True)
        marker = output_dir / '.grantdeck-export'
        existing_files = [path for path in output_dir.iterdir() if path != marker]
        if existing_files and not marker.exists():
            raise CommandError(
                f'Output directory {str(output_dir)!r} must be empty except for .grantdeck-export.'
            )
        if any(not path.is_file() or path.suffix != '.yml' for path in existing_files):
            raise CommandError(
                f'Output directory {str(output_dir)!r} contains unmanaged files.'
            )
        for path in existing_files:
            path.unlink()
        marker.touch()
        for document in documents:
            resource_name = document['metadata']['name']
            output_file = output_dir / f'{resource_name}.yml'
            output_file.write_text(
                json.dumps(document, indent=2, ensure_ascii=False) + '\n',
                encoding='utf-8',
            )

    def handle(self, *args, **options):
        documents = self.export_documents()
        output_dir = options.get('output_dir')

        if output_dir is not None:
            self.write_directory(Path(output_dir), documents)
            return

        if not documents:
            self.stdout.write('# No project model limits are configured.')
            return

        self.stdout.write('---')
        for index, document in enumerate(documents):
            if index:
                self.stdout.write('---')
            self.stdout.write(json.dumps(document, indent=2, ensure_ascii=False))
