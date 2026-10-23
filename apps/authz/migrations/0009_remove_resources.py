from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('authz', '0008_alter_projectmodellimit_max_parallel_requests'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='projectlimitclass',
            name='resources',
        ),
        migrations.RemoveField(
            model_name='project',
            name='resources',
        ),
        migrations.DeleteModel(name='TokenResourceUsage'),
        migrations.DeleteModel(name='ProjectResource'),
        migrations.DeleteModel(name='Resource'),
    ]
