from datetime import timedelta
from itertools import count
import logging

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from social_django.models import Association, Nonce, UserSocialAuth

from apps.authz.models import (
    MEMBERSHIP_ACTIVE,
    MEMBERSHIP_REQUESTED,
    Project,
    ProjectLimitClass,
    ProjectModelLimit,
    ProjectMembership,
    ProjectResource,
    Resource,
    StaticToken,
    TokenResourceUsage,
)


pytestmark = pytest.mark.django_db

project_shortcuts = count(1)


def create_project(**fields):
    fields.setdefault('shortcut', f'project-{next(project_shortcuts)}')
    return Project.objects.create(**fields)


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username='user')


@pytest.fixture
def token(user):
    project = create_project(name='Token project', end_date=timezone.localdate() + timedelta(days=30))
    allow_class_url(project, 'http://testserver/')
    return StaticToken.create_token(user, 'deploy', 30, project)


@pytest.fixture
def project(user):
    project = create_project(name='Active project', end_date=timezone.localdate() + timedelta(days=30))
    project.administrators.add(user)
    return project


def allow_class_url(project, url):
    resource, _ = Resource.objects.get_or_create(url=url)
    policy_class = project.limit_class
    if policy_class is None:
        policy_class = ProjectLimitClass.objects.create(
            name=f'Class for {project.shortcut}',
            slug=f'class-{project.shortcut.lower()}',
        )
        project.limit_class = policy_class
        project.save(update_fields=['limit_class'])
    policy_class.resources.add(resource)
    return resource


@pytest.fixture
def project_admin(user):
    return user


def authz(client, raw_token=None, method='get', authorization=None, path='/authz/check/', **headers):
    if raw_token:
        authorization = f'Bearer {raw_token}'
    kwargs = {'HTTP_AUTHORIZATION': authorization} if authorization else {}
    kwargs.update({f'HTTP_{key.upper().replace("-", "_")}': value for key, value in headers.items()})
    return getattr(client, method)(path, **kwargs)


def assert_empty(response, status):
    assert response.status_code == status
    assert response.content == b''


def project_admin_post_data(**fields):
    return {
        'shortcut': f'admin-project-{next(project_shortcuts)}',
        'projectmembership_set-TOTAL_FORMS': '0',
        'projectmembership_set-INITIAL_FORMS': '0',
        'projectmembership_set-MIN_NUM_FORMS': '0',
        'projectmembership_set-MAX_NUM_FORMS': '1000',
        'projectresource_set-TOTAL_FORMS': '0',
        'projectresource_set-INITIAL_FORMS': '0',
        'projectresource_set-MIN_NUM_FORMS': '0',
        'projectresource_set-MAX_NUM_FORMS': '1000',
        **fields,
    }


def test_create_token_stores_hash_only(user, token):
    stored, raw = token

    assert raw.startswith('gdk_')
    assert stored.token_hash != raw
    assert len(stored.token_hash) == 64
    assert stored.token_suffix == raw[-3:]
    assert stored.token_indicator == f'gdk_...{raw[-3:]}'
    assert raw[:-3] not in stored.token_hash
    assert stored.user == user
    assert stored.created_at


def test_project_gets_an_opaque_immutable_quota_key(project):
    original_key = project.quota_key
    assert original_key.startswith('qk_')
    assert len(original_key) < 48

    project.quota_key = 'qk_replaced'
    with pytest.raises(ValidationError):
        project.save(update_fields=['quota_key'])

    project.refresh_from_db()
    assert project.quota_key == original_key


def test_project_limit_class_resolves_endpoint_and_model_limit(project):
    resource = Resource.objects.create(url='http://testserver/api')
    policy_class = ProjectLimitClass.objects.create(name='Basic', slug='basic')
    policy_class.resources.add(resource)
    ProjectModelLimit.objects.create(
        limit_class=policy_class,
        model_name='bht/medium',
        daily_token_limit=12_000,
    )
    project.limit_class = policy_class
    project.save(update_fields=['limit_class'])

    assert project.resource_for_url('http://testserver/api/v1/chat/completions') == resource
    assert project.quota_limit_for_model('bht/medium') == 12_000
    assert project.quota_limit_for_model('bht/small') is None

    policy_class.slug = 'renamed-basic'
    with pytest.raises(ValidationError):
        policy_class.save(update_fields=['slug'])


def test_project_model_concurrency_limit_validates_values():
    policy_class = ProjectLimitClass(name='Basic', slug='basic')
    policy_class.save()
    model_limit = ProjectModelLimit(
        limit_class=policy_class,
        model_name='bht/large',
        daily_token_limit=12_000,
        max_parallel_requests=-1,
    )

    with pytest.raises(ValidationError):
        model_limit.full_clean()


def test_find_valid_returns_unexpired_token(token):
    stored, raw = token

    assert StaticToken.find_valid(raw) == stored


def test_find_valid_ignores_expired_token(token):
    stored, raw = token
    stored.expires_at = timezone.now() - timedelta(days=1)
    stored.save(update_fields=['expires_at'])

    assert StaticToken.find_valid(raw) is None


def test_find_valid_accepts_token_in_active_project(user, project):
    stored, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert StaticToken.find_valid(raw) == stored


def test_find_valid_ignores_token_in_ended_project(user, project):
    stored, raw = StaticToken.create_token(user, 'deploy', 30, project)
    project.end_date = timezone.localdate() - timedelta(days=1)
    project.save(update_fields=['end_date'])

    assert StaticToken.find_valid(raw) is None
    assert list(StaticToken.active_for_user(user)) == []


def test_deleting_project_deletes_related_tokens(user, project):
    token, _ = StaticToken.create_token(user, 'deploy', 30, project)

    project.delete()

    assert not StaticToken.objects.filter(pk=token.pk).exists()


def test_create_token_can_store_project(user, project):
    token, _ = StaticToken.create_token(user, 'deploy', 30, project)

    assert token.project == project


@pytest.mark.parametrize('shortcut', ['project_1', 'PROJECT-2', 'Project123'])
def test_project_shortcut_accepts_strict_http_token_values(shortcut):
    project = Project(name='Project', shortcut=shortcut)

    project.full_clean()


@pytest.mark.parametrize('shortcut', ['', 'has space', 'project.name', 'ümlaut', 'slash/value'])
def test_project_shortcut_rejects_non_token_safe_values(shortcut):
    project = Project(name='Project', shortcut=shortcut)

    with pytest.raises(ValidationError):
        project.full_clean()


def test_project_shortcut_is_unique():
    create_project(name='First', shortcut='shared')

    with pytest.raises(IntegrityError):
        create_project(name='Second', shortcut='shared')


def test_project_shortcut_max_length_is_enforced():
    project = Project(name='Project', shortcut='a' * 33)

    with pytest.raises(ValidationError):
        project.full_clean()


def test_resource_normalizes_url():
    resource = Resource.objects.create(url='HTTPS://Example.COM/api/?page=1#section', description='Model API')

    assert resource.url == 'https://example.com/api'
    assert resource.description == 'Model API'


def test_resource_matches_origin_and_path_segment_prefix():
    resource = Resource.objects.create(url='https://example.com/api')

    assert resource.matches_url('https://example.com/api')
    assert resource.matches_url('https://example.com/api/v1?query=ignored')
    assert not resource.matches_url('https://example.com/apiary')
    assert not resource.matches_url('https://other.example.com/api')


def test_project_resource_is_unique(project):
    resource = Resource.objects.create(url='http://testserver/api')
    ProjectResource.objects.create(project=project, resource=resource)

    with pytest.raises(IntegrityError):
        ProjectResource.objects.create(project=project, resource=resource)


def test_project_join_code_is_hashed(project):
    project.set_join_code('secret-code')
    project.save(update_fields=['join_code_hash'])

    assert project.join_code_hash != 'secret-code'
    assert project.check_join_code('secret-code')
    assert not project.check_join_code('wrong-code')


def test_project_find_by_join_code_returns_active_project(project):
    project.set_join_code('secret-code')
    project.save(update_fields=['join_code_hash'])

    assert Project.find_by_join_code('secret-code') == project
    assert Project.find_by_join_code('wrong-code') is None


def test_project_without_join_code_is_not_joinable(project):
    assert list(Project.joinable_projects()) == []
    assert Project.find_by_join_code('') is None


def test_ended_project_is_not_joinable(project):
    project.set_join_code('secret-code')
    project.end_date = timezone.localdate() - timedelta(days=1)
    project.save(update_fields=['join_code_hash', 'end_date'])

    assert Project.find_by_join_code('secret-code') is None


def test_project_join_with_approval_creates_requested_membership(user, project):
    project.set_join_code('secret-code')
    project.save(update_fields=['join_code_hash'])

    membership = Project.join_user_by_code(user, 'secret-code')

    assert membership.project == project
    assert membership.status == MEMBERSHIP_REQUESTED


def test_project_join_without_approval_creates_active_membership(user, project):
    project.set_join_code('secret-code')
    project.join_requires_approval = False
    project.save(update_fields=['join_code_hash', 'join_requires_approval'])

    membership = Project.join_user_by_code(user, 'secret-code')

    assert membership.project == project
    assert membership.status == MEMBERSHIP_ACTIVE


def test_project_join_without_approval_activates_existing_request(user, project):
    project.set_join_code('secret-code')
    project.join_requires_approval = False
    project.save(update_fields=['join_code_hash', 'join_requires_approval'])
    membership = ProjectMembership.objects.create(user=user, project=project)

    assert Project.join_user_by_code(user, 'secret-code') == membership
    membership.refresh_from_db()
    assert membership.status == MEMBERSHIP_ACTIVE


def test_token_projects_for_user_returns_active_memberships(user):
    active = create_project(name='Active member project', end_date=timezone.localdate() + timedelta(days=30))
    no_end = create_project(name='No end project')
    ended = create_project(name='Ended member project', end_date=timezone.localdate() - timedelta(days=1))
    requested = create_project(name='Requested project', end_date=timezone.localdate() + timedelta(days=30))
    ProjectMembership.objects.create(user=user, project=active, status=MEMBERSHIP_ACTIVE)
    ProjectMembership.objects.create(user=user, project=no_end, status=MEMBERSHIP_ACTIVE)
    ProjectMembership.objects.create(user=user, project=ended, status=MEMBERSHIP_ACTIVE)
    ProjectMembership.objects.create(user=user, project=requested)

    assert list(Project.token_projects_for_user(user)) == [active, no_end]


def test_project_membership_allows_token_creation_only_when_active(user):
    active_project = create_project(name='Active project')
    ended_project = create_project(name='Ended project', end_date=timezone.localdate() - timedelta(days=1))
    requested = ProjectMembership.objects.create(user=user, project=active_project)
    active = ProjectMembership.objects.create(
        user=user,
        project=ended_project,
        status=MEMBERSHIP_ACTIVE,
    )

    assert not requested.allows_token_creation
    assert not active.allows_token_creation
    requested.status = MEMBERSHIP_ACTIVE
    assert requested.allows_token_creation


def test_valid_bearer_token_allows_request(client, user, token):
    stored, raw = token

    response = authz(client, raw)

    assert_empty(response, 200)
    assert response.headers['x-current-user'] == user.username
    assert response.headers['x-current-project'] == stored.project.shortcut
    assert response.headers['x-current-project-id'] == str(stored.project.pk)
    assert response.headers['x-envoy-auth-headers-to-remove'] == 'authorization'


def test_allowed_authz_records_token_and_resource_usage(client, token):
    stored, raw = token

    assert_empty(authz(client, raw), 200)
    assert_empty(authz(client, raw), 200)
    stored.refresh_from_db()
    usage = TokenResourceUsage.objects.get(token=stored, resource__url='http://testserver/')

    assert stored.allowed_count == 2
    assert stored.denied_count == 0
    assert usage.allowed_count == 2


def test_valid_bearer_token_logs_result(client, user, token, caplog):
    _, raw = token

    with caplog.at_level(logging.INFO, logger='apps.authz.views'):
        authz(client, raw)

    record = caplog.records[0]
    assert record.authz_user == user.username
    assert record.authz_result == 'allowed'
    assert record.authz_status == 200


def test_authz_debug_logs_non_sensitive_incoming_headers(client, token, caplog):
    _, raw = token

    with caplog.at_level(logging.DEBUG, logger='apps.authz.views'):
        authz(
            client,
            raw,
            **{
                'x-ai-eg-model': 'bht/large',
                'x-request-id': 'request-123',
                'x-api-key': 'secret-api-key',
                'x-current-quota-key': 'qk-secret-bucket',
            },
        )

    debug_records = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert len(debug_records) == 1
    message = debug_records[0].getMessage().lower()
    assert "'x-ai-eg-model': 'bht/large'" in message
    assert "'x-request-id': 'request-123'" in message
    assert 'authorization' not in message
    assert 'secret-api-key' not in message
    assert 'x-api-key' not in message
    assert 'qk-secret-bucket' not in message
    assert 'x-current-quota-key' not in message


def test_project_token_allows_assigned_resource(client, user, project, caplog):
    allow_class_url(project, 'http://testserver/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    with caplog.at_level(logging.INFO, logger='apps.authz.views'):
        response = authz(client, raw, path='/authz/check/api/models?query=ignored')

    assert_empty(response, 200)
    assert response.headers['x-current-project'] == project.shortcut
    assert response.headers['x-current-project-id'] == str(project.pk)
    assert response.headers['x-current-quota-class'] == project.limit_class.slug
    assert response.headers['x-current-quota-key'] == project.quota_key
    record = caplog.records[0]
    assert record.authz_resource_url == 'http://testserver/api/models'


@override_settings(AUTHZ_CANONICAL_ORIGIN='https://api.example.test')
def test_project_token_uses_canonical_origin_for_internal_requests(client, user, project, caplog):
    allow_class_url(project, 'https://api.example.test/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    with caplog.at_level(logging.INFO, logger='apps.authz.views'):
        response = authz(client, raw, path='/authz/check/api/models')

    assert_empty(response, 200)
    assert caplog.records[0].authz_resource_url == 'https://api.example.test/api/models'


def test_project_token_denies_legacy_project_resource_without_class(client, user):
    project = create_project(name='Unassigned project')
    resource = Resource.objects.create(url='http://testserver/api')
    ProjectResource.objects.create(project=project, resource=resource)
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert_empty(authz(client, raw, path='/authz/check/api/models'), 403)


def test_project_token_ignores_model_header(client, user, project, caplog):
    allow_class_url(project, 'http://testserver/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    with caplog.at_level(logging.INFO, logger='apps.authz.views'):
        response = authz(client, raw, path='/authz/check/api/models', **{'x-ai-eg-model': 'bht/large'})

    assert_empty(response, 200)
    assert caplog.records[0].authz_model == ''


def test_project_token_does_not_deny_model(client, user, project):
    allow_class_url(project, 'http://testserver/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert_empty(authz(client, raw, path='/authz/check/api/models', **{'x-ai-eg-model': 'bht/small'}), 200)


def test_url_only_request_does_not_require_model_grant(client, user, project):
    allow_class_url(project, 'http://testserver/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert_empty(authz(client, raw, path='/authz/check/api/models'), 200)


def test_model_header_does_not_replace_url_grant(client, user, project):
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert_empty(authz(client, raw, path='/authz/check/api/models', **{'x-ai-eg-model': 'bht/large'}), 403)


def test_project_token_denies_unassigned_resource(client, user, project):
    allow_class_url(project, 'http://testserver/api')
    stored, raw = StaticToken.create_token(user, 'deploy', 30, project)

    response = authz(client, raw, path='/authz/check/other')

    assert_empty(response, 403)
    stored.refresh_from_db()
    assert stored.allowed_count == 0
    assert stored.denied_count == 1
    assert 'x-current-project' not in response.headers
    assert not TokenResourceUsage.objects.filter(token=stored).exists()


def test_token_in_project_without_end_date_allows_assigned_resource(client, user):
    project = create_project(name='No end project')
    allow_class_url(project, 'http://testserver/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert_empty(authz(client, raw, path='/authz/check/api'), 200)


def test_project_token_enforces_path_segment_boundary(client, user, project):
    allow_class_url(project, 'http://testserver/api')
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)

    assert_empty(authz(client, raw, path='/authz/check/apiary'), 403)


@pytest.mark.parametrize('authorization', [None, 'Basic abc123', 'Bearer other-token'])
def test_malformed_authorization_returns_unauthorized(client, authorization):
    response = authz(client, authorization=authorization)

    assert_empty(response, 401)
    assert response.headers['WWW-Authenticate'] == 'Bearer'


def test_unknown_bearer_token_returns_forbidden(client):
    assert_empty(authz(client, 'gdk_unknown'), 403)
    assert not TokenResourceUsage.objects.exists()


def test_unknown_bearer_token_logs_result(client, caplog):
    with caplog.at_level(logging.INFO, logger='apps.authz.views'):
        authz(client, 'gdk_unknown')

    record = caplog.records[0]
    assert record.authz_user == ''
    assert record.authz_result == 'forbidden'
    assert record.authz_status == 403


def test_expired_bearer_token_returns_forbidden(client, token):
    stored, raw = token
    stored.expires_at = timezone.now() - timedelta(days=1)
    stored.save(update_fields=['expires_at'])

    assert_empty(authz(client, raw), 403)
    stored.refresh_from_db()
    assert stored.allowed_count == 0
    assert stored.denied_count == 0
    assert not TokenResourceUsage.objects.exists()


def test_token_in_ended_project_returns_forbidden(client, user, project):
    _, raw = StaticToken.create_token(user, 'deploy', 30, project)
    project.end_date = timezone.localdate() - timedelta(days=1)
    project.save(update_fields=['end_date'])

    assert_empty(authz(client, raw), 403)


def test_valid_bearer_token_allows_post_without_csrf_token(token):
    _, raw = token

    response = authz(Client(enforce_csrf_checks=True), raw, method='post')

    assert response.status_code == 200


def test_adding_user_to_project_administrators_makes_user_staff(user):
    project = create_project(name='Managed')

    project.administrators.add(user)
    user.refresh_from_db()

    assert user.is_staff


def test_group_model_is_not_registered_in_admin():
    assert Group not in django_admin.site._registry


def test_python_social_auth_models_are_not_registered_in_admin():
    assert UserSocialAuth not in django_admin.site._registry
    assert Nonce not in django_admin.site._registry
    assert Association not in django_admin.site._registry


def test_project_admin_cannot_create_projects(client, project_admin):
    existing = create_project(name='Existing')
    existing.administrators.add(project_admin)
    client.force_login(project_admin)

    response = client.post(
        reverse('admin:authz_project_add'),
        {
            'name': 'First project',
            'end_date': timezone.localdate() + timedelta(days=30),
            'join_requires_approval': 'on',
            '_save': 'Save',
        },
    )

    assert response.status_code == 403
    assert not Project.objects.filter(name='First project').exists()


def test_superuser_can_create_project_with_shortcut(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username='super',
        email='super@example.test',
        password='correct-password',
    )
    client.force_login(superuser)

    response = client.post(
        reverse('admin:authz_project_add'),
        project_admin_post_data(
            name='First project',
            shortcut='first_project',
            administrators=[superuser.pk],
            join_requires_approval='on',
            _save='Save',
        ),
    )

    assert response.status_code == 302
    assert Project.objects.filter(name='First project', shortcut='first_project').exists()


def test_admin_rejects_duplicate_project_shortcut(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username='super',
        email='super@example.test',
        password='correct-password',
    )
    create_project(name='First', shortcut='shared')
    client.force_login(superuser)

    response = client.post(
        reverse('admin:authz_project_add'),
        project_admin_post_data(
            name='Second',
            shortcut='shared',
            administrators=[superuser.pk],
            _save='Save',
        ),
    )

    assert response.status_code == 200
    assert not Project.objects.filter(name='Second').exists()


def test_admin_rejects_unsafe_project_shortcut(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username='super',
        email='super@example.test',
        password='correct-password',
    )
    client.force_login(superuser)

    response = client.post(
        reverse('admin:authz_project_add'),
        project_admin_post_data(
            name='Unsafe',
            shortcut='unsafe.value',
            administrators=[superuser.pk],
            _save='Save',
        ),
    )

    assert response.status_code == 200
    assert not Project.objects.filter(name='Unsafe').exists()


def test_superuser_can_set_replace_and_clear_project_join_code(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username='super',
        email='super@example.test',
        password='correct-password',
    )
    project = create_project(name='Managed')
    client.force_login(superuser)
    url = reverse('admin:authz_project_change', args=[project.pk])

    assert client.post(
        url,
        project_admin_post_data(
            name='Managed',
            administrators=[superuser.pk],
            join_requires_approval='on',
            join_code='first',
            _save='Save',
        ),
    ).status_code == 302
    project.refresh_from_db()
    assert project.check_join_code('first')
    assert project.join_code_hash != 'first'

    assert client.post(
        url,
        project_admin_post_data(
            name='Managed',
            administrators=[superuser.pk],
            join_code='second',
            _save='Save',
        ),
    ).status_code == 302
    project.refresh_from_db()
    assert project.check_join_code('second')
    assert not project.join_requires_approval

    assert client.post(
        url,
        project_admin_post_data(
            name='Managed',
            administrators=[superuser.pk],
            clear_join_code='on',
            _save='Save',
        ),
    ).status_code == 302
    project.refresh_from_db()
    assert project.join_code_hash is None


def test_duplicate_project_join_code_is_rejected_in_admin(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username='super',
        email='super@example.test',
        password='correct-password',
    )
    first = create_project(name='First')
    first.set_join_code('shared')
    first.save(update_fields=['join_code_hash'])
    second = create_project(name='Second')
    client.force_login(superuser)

    response = client.post(
        reverse('admin:authz_project_change', args=[second.pk]),
        project_admin_post_data(
            name='Second',
            administrators=[superuser.pk],
            join_code='shared',
            _save='Save',
        ),
    )
    second.refresh_from_db()

    assert response.status_code == 200
    assert second.join_code_hash is None


def test_project_admin_can_change_administered_project_properties(client, project_admin):
    managed = create_project(name='Managed')
    managed.administrators.add(project_admin)
    client.force_login(project_admin)

    response = client.post(
        reverse('admin:authz_project_change', args=[managed.pk]),
        {'name': 'Managed', 'shortcut': managed.shortcut, 'join_code': 'managed-code', '_save': 'Save'},
    )
    managed.refresh_from_db()

    assert response.status_code == 302
    assert managed.check_join_code('managed-code')


def test_project_admin_sees_only_administered_projects(client, user, project_admin):
    managed = create_project(name='Managed', end_date=timezone.localdate() + timedelta(days=30))
    managed.administrators.add(project_admin)
    allow_class_url(managed, 'http://testserver/api')
    StaticToken.create_token(user, 'managed-token-1', 30, managed)
    StaticToken.create_token(user, 'managed-token-2', 30, managed)
    create_project(name='Other', end_date=timezone.localdate() + timedelta(days=30))
    client.force_login(project_admin)

    response = client.get(reverse('admin:authz_project_changelist'))

    assert response.status_code == 200
    projects = list(response.context['cl'].queryset)
    assert projects == [managed]
    assert projects[0].limit_class == managed.limit_class


def test_project_administrator_can_use_membership_admin(client, user):
    managed = create_project(name='Managed')
    managed.administrators.add(user)
    membership = ProjectMembership.objects.create(user=user, project=managed)
    client.force_login(user)

    response = client.get(reverse('admin:authz_projectmembership_changelist'))

    assert response.status_code == 200
    assert list(response.context['cl'].queryset) == [membership]


def test_project_admin_cannot_change_unrelated_project(client, project_admin):
    create_project(name='Managed').administrators.add(project_admin)
    other = create_project(name='Other', end_date=timezone.localdate() + timedelta(days=30))
    client.force_login(project_admin)

    response = client.get(reverse('admin:authz_project_change', args=[other.pk]))

    assert response.status_code != 200


def test_project_admin_cannot_delete_administered_project(client, project_admin):
    managed = create_project(name='Managed')
    managed.administrators.add(project_admin)
    client.force_login(project_admin)

    response = client.post(reverse('admin:authz_project_delete', args=[managed.pk]), {'post': 'yes'})

    assert response.status_code == 403
    assert Project.objects.filter(pk=managed.pk).exists()


def test_project_admin_cannot_use_bearer_token_admin(client, user, project_admin):
    managed = create_project(name='Managed', end_date=timezone.localdate() + timedelta(days=30))
    managed.administrators.add(project_admin)
    other = create_project(name='Other', end_date=timezone.localdate() + timedelta(days=30))
    StaticToken.create_token(user, 'managed-token', 30, managed)
    StaticToken.create_token(user, 'other-token', 30, other)
    client.force_login(project_admin)

    response = client.get(reverse('admin:authz_statictoken_changelist'))

    assert response.status_code == 403


def test_project_admin_cannot_view_tokens_in_admin(client, user, project_admin):
    managed = create_project(name='Managed', end_date=timezone.localdate() + timedelta(days=30))
    managed.administrators.add(project_admin)
    token, _ = StaticToken.create_token(user, 'managed-token', 30, managed)
    client.force_login(project_admin)

    change_url = reverse('admin:authz_statictoken_change', args=[token.pk])

    assert client.get(change_url).status_code == 403


def test_project_admin_cannot_delete_administered_project_token(client, user, project_admin):
    managed = create_project(name='Managed', end_date=timezone.localdate() + timedelta(days=30))
    managed.administrators.add(project_admin)
    token, _ = StaticToken.create_token(user, 'managed-token', 30, managed)
    client.force_login(project_admin)

    response = client.post(reverse('admin:authz_statictoken_delete', args=[token.pk]), {'post': 'yes'})

    assert response.status_code == 403
    assert StaticToken.objects.filter(pk=token.pk).exists()


def test_project_admin_cannot_create_tokens_in_admin(client, project_admin):
    create_project(name='Managed').administrators.add(project_admin)
    client.force_login(project_admin)

    response = client.get(reverse('admin:authz_statictoken_add'))

    assert response.status_code == 403


def test_project_admin_sees_only_administered_project_memberships(client, user, project_admin):
    managed = create_project(name='Managed', end_date=timezone.localdate() + timedelta(days=30))
    managed.administrators.add(project_admin)
    other = create_project(name='Other', end_date=timezone.localdate() + timedelta(days=30))
    managed_membership = ProjectMembership.objects.create(user=user, project=managed)
    ProjectMembership.objects.create(user=user, project=other)
    client.force_login(project_admin)

    response = client.get(reverse('admin:authz_projectmembership_changelist'))

    assert response.status_code == 200
    assert list(response.context['cl'].queryset) == [managed_membership]


def test_project_admin_can_activate_administered_project_membership(client, user, project_admin):
    managed = create_project(name='Managed', end_date=timezone.localdate() + timedelta(days=30))
    managed.administrators.add(project_admin)
    membership = ProjectMembership.objects.create(user=user, project=managed)
    client.force_login(project_admin)

    response = client.post(
        reverse('admin:authz_projectmembership_change', args=[membership.pk]),
        {'project': managed.pk, 'user': user.pk, 'status': MEMBERSHIP_ACTIVE, '_save': 'Save'},
    )
    membership.refresh_from_db()

    assert response.status_code == 302
    assert membership.status == MEMBERSHIP_ACTIVE


def test_project_admin_cannot_change_unrelated_project_membership(client, user, project_admin):
    create_project(name='Managed').administrators.add(project_admin)
    other = create_project(name='Other', end_date=timezone.localdate() + timedelta(days=30))
    membership = ProjectMembership.objects.create(user=user, project=other)
    client.force_login(project_admin)

    response = client.get(reverse('admin:authz_projectmembership_change', args=[membership.pk]))

    assert response.status_code != 200


def test_superuser_can_manage_resources_in_admin(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username='super',
        email='super@example.test',
        password='correct-password',
    )
    client.force_login(superuser)

    assert client.get(reverse('admin:authz_resource_changelist')).status_code == 200
    assert ProjectResource not in django_admin.site._registry


def test_project_admin_cannot_manage_resources_in_admin(client, project_admin):
    create_project(name='Managed').administrators.add(project_admin)
    client.force_login(project_admin)

    assert client.get(reverse('admin:authz_resource_changelist')).status_code == 403
    assert ProjectResource not in django_admin.site._registry
