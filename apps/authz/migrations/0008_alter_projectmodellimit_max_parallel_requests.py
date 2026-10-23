from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('authz', '0007_remove_project_model_pending_limit'),
    ]

    operations = [
        migrations.AlterField(
            model_name='projectmodellimit',
            name='max_parallel_requests',
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    'Maximum number of requests Envoy may send to this model at once, '
                    'counted separately for each Envoy pod.'
                ),
                null=True,
                validators=[MinValueValidator(1)],
            ),
        ),
    ]
