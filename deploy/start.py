import logging
import os
from importlib.metadata import version

import django
from django.db import connection
from django.core.management import call_command


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "grantdeck.settings")
    django.setup()
    release_tag = os.environ.get('GDK_RELEASE_TAG') or version('grantdeck')
    logging.getLogger('apps').info('Starting GrantDeck version %s', release_tag)
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(73918421)")
            try:
                call_command("migrate", interactive=False)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(73918421)")
    else:
        call_command("migrate", interactive=False)
    os.execvp(
        "python",
        [
            "python",
            "-m",
            "gunicorn",
            "grantdeck.wsgi:application",
            "--bind",
            "0.0.0.0:8000",
        ],
    )


if __name__ == "__main__":
    main()
