from django.core.validators import MinValueValidator
from django.db import migrations, models


def populate_initial_concurrency_limits(apps, schema_editor):
    ProjectModelLimit = apps.get_model('authz', 'ProjectModelLimit')
    initial_limits = {
        'bht/large': (16, 32),
        'bht/medium': (8, 16),
        'bht/small': (1, 4),
    }
    for model_name, (parallel, pending) in initial_limits.items():
        ProjectModelLimit.objects.filter(model_name=model_name).update(
            max_parallel_requests=parallel,
            max_pending_requests=pending,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('authz', '0005_project_limit_classes'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectmodellimit',
            name='max_parallel_requests',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Maximum number of requests Envoy may send to this model at once.',
                null=True,
                validators=[MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name='projectmodellimit',
            name='max_pending_requests',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Maximum number of requests Envoy may queue for this model.',
                null=True,
                validators=[MinValueValidator(0)],
            ),
        ),
        migrations.AddConstraint(
            model_name='projectmodellimit',
            constraint=models.CheckConstraint(
                condition=models.Q(max_parallel_requests__isnull=True) | models.Q(max_parallel_requests__gt=0),
                name='project_model_parallel_limit_positive',
            ),
        ),
        migrations.AddConstraint(
            model_name='projectmodellimit',
            constraint=models.CheckConstraint(
                condition=models.Q(max_pending_requests__isnull=True) | models.Q(max_pending_requests__gte=0),
                name='project_model_pending_limit_nonnegative',
            ),
        ),
        migrations.RunPython(populate_initial_concurrency_limits, migrations.RunPython.noop),
    ]
