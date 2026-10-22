from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('authz', '0006_project_model_concurrency_limits'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='projectmodellimit',
            name='project_model_pending_limit_nonnegative',
        ),
        migrations.RemoveField(
            model_name='projectmodellimit',
            name='max_pending_requests',
        ),
    ]
