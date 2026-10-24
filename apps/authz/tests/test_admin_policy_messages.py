import pytest
from django.contrib import admin
from django.contrib import messages
from django.urls import reverse

from apps.authz import admin as authz_admin
from apps.authz.admin import ENVOY_POLICY_UPDATE_MESSAGE
from apps.authz.kubernetes import PolicyDeploymentResult
from apps.authz.models import (
    Project,
    ProjectLimitClass,
    ProjectMembership,
    ProjectModelLimit,
)


pytestmark = pytest.mark.django_db


def admin_messages(response):
    return list(response.wsgi_request._messages)


def post_limit_class(client, policy_class=None, *, name=None, rows=()):
    existing = list(policy_class.model_limits.order_by('model_name')) if policy_class else []
    url = (
        reverse('admin:authz_projectlimitclass_change', args=[policy_class.pk])
        if policy_class
        else reverse('admin:authz_projectlimitclass_add')
    )
    data = {
        'name': name or (policy_class.name if policy_class else 'Research'),
        'slug': policy_class.slug if policy_class else 'research',
        'model_limits-TOTAL_FORMS': str(max(len(existing), len(rows))),
        'model_limits-INITIAL_FORMS': str(len(existing)),
        'model_limits-MIN_NUM_FORMS': '0',
        'model_limits-MAX_NUM_FORMS': '1000',
        '_save': 'Save',
    }
    for index, row in enumerate(rows):
        if index < len(existing):
            data[f'model_limits-{index}-id'] = str(existing[index].pk)
            data[f'model_limits-{index}-limit_class'] = str(policy_class.pk)
        if row.get('id'):
            data[f'model_limits-{index}-id'] = str(row['id'])
            data[f'model_limits-{index}-limit_class'] = str(policy_class.pk)
        data[f'model_limits-{index}-model_name'] = row.get('model_name', '')
        data[f'model_limits-{index}-daily_token_limit'] = str(row.get('daily_token_limit', ''))
        data[f'model_limits-{index}-max_parallel_requests'] = str(row.get('max_parallel_requests', ''))
        if row.get('delete'):
            data[f'model_limits-{index}-DELETE'] = 'on'

    # Django renders one extra blank inline form for additions and changes.
    total_forms = int(data['model_limits-TOTAL_FORMS'])
    if total_forms == len(existing):
        total_forms += 1
        data['model_limits-TOTAL_FORMS'] = str(total_forms)
        index = total_forms - 1
        data[f'model_limits-{index}-id'] = ''
        data[f'model_limits-{index}-limit_class'] = str(policy_class.pk) if policy_class else ''
        data[f'model_limits-{index}-model_name'] = ''
        data[f'model_limits-{index}-daily_token_limit'] = ''
        data[f'model_limits-{index}-max_parallel_requests'] = ''

    return client.post(url, data)


def assert_policy_warning(response):
    warning_messages = [message for message in admin_messages(response) if message.level == messages.WARNING]
    assert [str(message) for message in warning_messages] == [ENVOY_POLICY_UPDATE_MESSAGE]


def assert_no_policy_warning(response):
    assert not any(message.level == messages.WARNING for message in admin_messages(response))


@pytest.mark.parametrize('action', ['create', 'assign', 'reassign', 'clear', 'rename', 'unchanged'])
def test_project_policy_update_warning(client, django_user_model, action):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    project = None
    if action != 'create':
        project = Project.objects.create(
            name='Project', shortcut='project',
            limit_class=None if action == 'assign' else policy_class,
        )
    if action == 'reassign':
        policy_class = ProjectLimitClass.objects.create(name='Other', slug='other')
    client.force_login(admin_user)

    response = client.post(
        reverse('admin:authz_project_change', args=[project.pk])
        if project else reverse('admin:authz_project_add'),
        {
            'name': 'Renamed' if action == 'rename' else 'Project',
            'shortcut': 'project',
            'administrators': [admin_user.pk],
            'limit_class': '' if action == 'clear' else str(policy_class.pk),
            'join_requires_approval': 'on',
            'projectmembership_set-TOTAL_FORMS': '0',
            'projectmembership_set-INITIAL_FORMS': '0',
            '_save': 'Save',
        },
    )

    assert response.status_code == 302
    project = Project.objects.get(shortcut='project')
    assert project.limit_class_id == (None if action == 'clear' else policy_class.pk)
    assert project.name == ('Renamed' if action == 'rename' else 'Project')
    if action in {'create', 'assign', 'reassign', 'clear'}:
        assert_policy_warning(response)
    else:
        assert_no_policy_warning(response)


def test_user_admin_lists_project_names(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    user = django_user_model.objects.create_user(username='researcher')
    first = Project.objects.create(name='First Project', shortcut='first')
    second = Project.objects.create(name='Second Project', shortcut='second')
    ProjectMembership.objects.create(user=user, project=first)
    ProjectMembership.objects.create(user=user, project=second)
    client.force_login(admin_user)

    response = client.get(reverse('admin:auth_user_changelist'))

    assert response.status_code == 200
    assert b'Projects' in response.content
    assert b'First Project, Second Project' in response.content


def test_admin_warns_when_adding_model_limit(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    client.force_login(admin_user)

    response = post_limit_class(
        client,
        rows=[{'model_name': 'bht/large', 'daily_token_limit': 100_000}],
    )

    assert response.status_code == 302
    policy_class = ProjectLimitClass.objects.get(slug='research')
    assert policy_class.model_limits.filter(model_name='bht/large').exists()
    assert_policy_warning(response)


def test_admin_warns_when_changing_model_limit(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    model_limit = ProjectModelLimit.objects.create(
        limit_class=policy_class, model_name='bht/large', daily_token_limit=100_000,
    )
    client.force_login(admin_user)

    response = post_limit_class(
        client,
        policy_class,
        rows=[{
            'id': model_limit.pk,
            'model_name': model_limit.model_name,
            'daily_token_limit': 200_000,
            'max_parallel_requests': 12,
        }],
    )

    assert response.status_code == 302
    model_limit.refresh_from_db()
    assert model_limit.daily_token_limit == 200_000
    assert model_limit.max_parallel_requests == 12
    assert_policy_warning(response)


def test_model_limit_has_dedicated_admin(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    model_limit = ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=100_000,
        max_parallel_requests=16,
    )
    Project.objects.create(name='First', shortcut='first', limit_class=policy_class)
    Project.objects.create(name='Second', shortcut='second', limit_class=policy_class)
    client.force_login(admin_user)

    assert ProjectModelLimit in admin.site._registry
    response = client.get(reverse('admin:authz_projectmodellimit_changelist'))

    assert response.status_code == 200
    assert b'bht/large' in response.content
    assert b'Research' in response.content
    assert b'Projects' in response.content
    assert response.context['cl'].result_list[0].project_count_value == 2
    assert b'Deploy all Envoy policies' not in response.content

    response = client.get(reverse('admin:index'))

    assert response.status_code == 200
    assert b'Deploy all Envoy policies' in response.content
    assert reverse('admin:authz_projectmodellimit_deploy_policies').encode() in response.content


def test_model_limit_admin_explains_parallel_limit_scope(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    model_limit = ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=100_000,
    )
    client.force_login(admin_user)

    response = client.get(
        reverse('admin:authz_projectmodellimit_change', args=[model_limit.pk]),
    )

    assert response.status_code == 200
    assert b'counted separately for each Envoy pod' in response.content


def test_model_limit_admin_deploys_all_policies(client, django_user_model, monkeypatch):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    calls = []
    monkeypatch.setattr(
        authz_admin,
        'deploy_all_policies',
        lambda **kwargs: calls.append(kwargs) or PolicyDeploymentResult(11, 2, 'grantdeck'),
    )
    client.force_login(admin_user)

    response = client.post(reverse('admin:authz_projectmodellimit_deploy_policies'))

    assert response.status_code == 302
    assert calls == [{'actor': 'admin'}]
    success_messages = [
        str(message)
        for message in admin_messages(response)
        if message.level == messages.SUCCESS
    ]
    assert success_messages == [
        'Deployed 11 Envoy policy resources in grantdeck; pruned 2 stale resources.'
    ]


def test_model_limit_admin_deployment_endpoint_is_post_only(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    client.force_login(admin_user)

    response = client.get(reverse('admin:authz_projectmodellimit_deploy_policies'))

    assert response.status_code == 405


def test_model_limit_admin_warns_when_changing_policy(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    model_limit = ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=100_000,
        max_parallel_requests=16,
    )
    client.force_login(admin_user)

    response = client.post(
        reverse('admin:authz_projectmodellimit_change', args=[model_limit.pk]),
        {
            'limit_class': policy_class.pk,
            'model_name': 'bht/large',
            'daily_token_limit': 200_000,
            'max_parallel_requests': 8,
            '_save': 'Save',
        },
    )

    assert response.status_code == 302
    model_limit.refresh_from_db()
    assert model_limit.daily_token_limit == 200_000
    assert model_limit.max_parallel_requests == 8
    assert_policy_warning(response)


def test_admin_warns_when_deleting_model_limit_inline(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    model_limit = ProjectModelLimit.objects.create(
        limit_class=policy_class, model_name='bht/large', daily_token_limit=100_000,
    )
    client.force_login(admin_user)

    response = post_limit_class(
        client,
        policy_class,
        rows=[{
            'id': model_limit.pk,
            'model_name': model_limit.model_name,
            'daily_token_limit': model_limit.daily_token_limit,
            'delete': True,
        }],
    )

    assert response.status_code == 302
    assert not ProjectModelLimit.objects.filter(pk=model_limit.pk).exists()
    assert_policy_warning(response)


def test_admin_warns_when_deleting_class_with_model_limits(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    ProjectModelLimit.objects.create(
        limit_class=policy_class, model_name='bht/large', daily_token_limit=100_000,
    )
    client.force_login(admin_user)

    response = client.post(
        reverse('admin:authz_projectlimitclass_delete', args=[policy_class.pk]),
        {'post': 'yes'},
    )

    assert response.status_code == 302
    assert not ProjectLimitClass.objects.filter(pk=policy_class.pk).exists()
    assert_policy_warning(response)


def test_admin_does_not_warn_for_class_name_change(client, django_user_model):
    admin_user = django_user_model.objects.create_superuser(
        username='admin', email='admin@example.test', password='password',
    )
    policy_class = ProjectLimitClass.objects.create(name='Research', slug='research')
    client.force_login(admin_user)

    response = post_limit_class(
        client,
        policy_class,
        name='Research access',
    )

    assert response.status_code == 302
    assert_no_policy_warning(response)
