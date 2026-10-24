import hashlib
import re


def exported_route_name(project, model_name):
    model_slug = re.sub(r'[^a-z0-9]+', '-', model_name.lower()).strip('-')[:24].strip('-') or 'model'
    digest = hashlib.sha256(model_name.encode()).hexdigest()[:8]
    return f'grantdeck-p{project.pk}-{model_slug}-{digest}'
