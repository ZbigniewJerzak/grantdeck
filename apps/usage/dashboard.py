from datetime import datetime, timedelta, timezone

from apps.authz.models import StaticToken
from apps.usage.models import DailyUsage


def usage_rows_for_projects(projects, *, user=None, include_all_users=False):
    """Build dashboard rows from GrantDeck's durable daily usage aggregates."""
    projects = list(projects)
    project_ids = {project.pk for project in projects}
    today = datetime.now(timezone.utc).date()

    if include_all_users:
        identities = {
            (token.user.get_username(), token.project_id)
            for token in StaticToken.objects.filter(
                StaticToken.active_filter(), project_id__in=project_ids
            ).select_related('user')
        }
        for project in projects:
            if not any(project_identifier == project.pk for _, project_identifier in identities):
                identities.add(('', project.pk))
    else:
        identities = {(user.get_username(), project.pk) for project in projects}

    usage = {
        (row.user_identifier, row.project_identifier, row.model_name): row.token_count
        for row in DailyUsage.objects.filter(day=today, project_identifier__in=project_ids)
    }
    reset_at = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    rows = []
    for project in projects:
        policy_class = project.limit_class
        limits = list(policy_class.model_limits.all()) if policy_class else []
        project_identities = sorted(
            username or '—' for username, project_identifier in identities if project_identifier == project.pk
        )
        if not project_identities and not include_all_users:
            project_identities = [user.get_username()]
        for username in project_identities:
            if not limits:
                rows.append({
                    'project': project,
                    'policy_class': policy_class,
                    'user_identifier': username,
                    'model_name': None,
                    'consumed_tokens': None,
                    'daily_token_limit': None,
                    'max_parallel_requests': None,
                    'reset_at': None,
                })
                continue
            for model_limit in limits:
                rows.append({
                    'project': project,
                    'policy_class': policy_class,
                    'user_identifier': username,
                    'model_name': model_limit.model_name,
                    'consumed_tokens': usage.get((username, project.pk, model_limit.model_name), 0),
                    'daily_token_limit': model_limit.daily_token_limit,
                    'max_parallel_requests': model_limit.max_parallel_requests,
                    'reset_at': reset_at,
                })
    return rows
