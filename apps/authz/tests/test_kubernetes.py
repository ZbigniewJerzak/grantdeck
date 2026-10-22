from pathlib import Path

import pytest

from apps.authz import kubernetes


class FakeCustomObjectsApi:
    def __init__(self, items=None, fail_apply=False):
        self.items = items or {}
        self.fail_apply = fail_apply
        self.applied = []
        self.listed = []
        self.deleted = []
        self.api_client = self

    def call_api(self, *args, **kwargs):
        self.applied.append((args, kwargs))
        if self.fail_apply:
            raise RuntimeError('apply failed')

    def list_namespaced_custom_object(self, **kwargs):
        self.listed.append(kwargs)
        return {'items': self.items.get(kwargs['plural'], [])}

    def delete_namespaced_custom_object(self, **kwargs):
        self.deleted.append(kwargs)


def document(kind, name):
    group, version, _ = kubernetes.RESOURCE_TYPES[kind]
    return {
        'apiVersion': f'{group}/{version}',
        'kind': kind,
        'metadata': {'name': name},
        'spec': {},
    }


def configure(monkeypatch, tmp_path, api, documents):
    namespace_file = tmp_path / 'namespace'
    namespace_file.write_text('test-namespace\n')
    monkeypatch.setattr(kubernetes, 'NAMESPACE_FILE', Path(namespace_file))
    monkeypatch.setattr(kubernetes, '_load_kubernetes_client', lambda: api)
    monkeypatch.setattr(kubernetes, 'build_policy_documents', lambda: documents)


def test_deployment_applies_in_dependency_order_and_prunes_stale_resources(monkeypatch, tmp_path):
    documents = [
        document('SecurityPolicy', 'auth'),
        document('BackendTrafficPolicy', 'quota'),
        document('AIGatewayRoute', 'route'),
    ]
    api = FakeCustomObjectsApi(items={
        'aigatewayroutes': [
            {'metadata': {'name': 'route'}},
            {'metadata': {'name': 'stale-route'}},
        ],
        'backendtrafficpolicies': [
            {'metadata': {'name': 'quota'}},
            {'metadata': {'name': 'stale-quota'}},
        ],
        'securitypolicies': [{'metadata': {'name': 'auth'}}],
    })
    configure(monkeypatch, tmp_path, api, documents)

    result = kubernetes.deploy_all_policies(actor='admin')

    assert [call[1]['body']['kind'] for call in api.applied] == [
        'AIGatewayRoute', 'BackendTrafficPolicy', 'SecurityPolicy',
    ]
    assert all(
        call[1]['path_params']['namespace'] == 'test-namespace'
        for call in api.applied
    )
    assert all(
        ('fieldManager', kubernetes.FIELD_MANAGER) in call[1]['query_params']
        for call in api.applied
    )
    assert all(('force', False) in call[1]['query_params'] for call in api.applied)
    assert all(
        call[1]['header_params']['Content-Type'] == 'application/apply-patch+yaml'
        for call in api.applied
    )
    assert [call['name'] for call in api.deleted] == ['stale-quota', 'stale-route']
    assert result == kubernetes.PolicyDeploymentResult(
        applied=3,
        pruned=2,
        namespace='test-namespace',
    )


def test_failed_apply_never_lists_or_prunes_resources(monkeypatch, tmp_path):
    api = FakeCustomObjectsApi(fail_apply=True)
    configure(monkeypatch, tmp_path, api, [document('AIGatewayRoute', 'route')])

    with pytest.raises(kubernetes.PolicyDeploymentError, match='rejected'):
        kubernetes.deploy_all_policies(actor='admin')

    assert api.listed == []
    assert api.deleted == []


def test_empty_desired_state_prunes_every_managed_resource(monkeypatch, tmp_path):
    api = FakeCustomObjectsApi(items={
        'aigatewayroutes': [{'metadata': {'name': 'route'}}],
        'backendtrafficpolicies': [{'metadata': {'name': 'quota'}}],
        'securitypolicies': [{'metadata': {'name': 'auth'}}],
    })
    configure(monkeypatch, tmp_path, api, [])

    result = kubernetes.deploy_all_policies(actor='admin')

    assert api.applied == []
    assert [call['name'] for call in api.deleted] == ['auth', 'quota', 'route']
    assert result.pruned == 3


def test_unavailable_namespace_fails_before_loading_client(monkeypatch, tmp_path):
    monkeypatch.setattr(kubernetes, 'NAMESPACE_FILE', tmp_path / 'missing')
    monkeypatch.setattr(kubernetes, 'build_policy_documents', lambda: [])

    with pytest.raises(kubernetes.PolicyDeploymentError, match='namespace'):
        kubernetes.deploy_all_policies(actor='admin')
