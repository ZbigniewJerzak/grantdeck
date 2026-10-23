import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


TOKEN_PREFIX = 'gdk_'
TOKEN_SECRET_BYTES = 32
MEMBERSHIP_REQUESTED = 'requested'
MEMBERSHIP_ACTIVE = 'active'
MEMBERSHIP_STATUS_CHOICES = [
    (MEMBERSHIP_REQUESTED, 'Requested'),
    (MEMBERSHIP_ACTIVE, 'Active'),
]
PROJECT_SHORTCUT_VALIDATOR = RegexValidator(
    r'^[A-Za-z0-9_-]+$',
    'Use only ASCII letters, digits, underscores, and hyphens.',
)
PROJECT_QUOTA_CLASS_SLUG_VALIDATOR = RegexValidator(
    r'^[a-z][a-z0-9-]*$',
    'Use a lowercase slug beginning with a letter.',
)


def generate_project_quota_key():
    return f'qk_{secrets.token_urlsafe(32)}'


class ProjectLimitClass(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(
        max_length=48,
        unique=True,
        validators=[PROJECT_QUOTA_CLASS_SLUG_VALIDATOR],
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Project limit class'
        verbose_name_plural = 'Project limit classes'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk:
            previous_slug = type(self).objects.filter(pk=self.pk).values_list('slug', flat=True).first()
            if previous_slug and self.slug != previous_slug:
                raise ValidationError({'slug': 'A project limit class slug is immutable.'})
        super().save(*args, **kwargs)

class Project(models.Model):
    name = models.CharField(max_length=120)
    shortcut = models.CharField(
        max_length=32,
        unique=True,
        validators=[PROJECT_SHORTCUT_VALIDATOR],
    )
    end_date = models.DateField(blank=True, null=True, db_index=True)
    join_code_hash = models.CharField(max_length=64, blank=True, null=True, unique=True)
    join_requires_approval = models.BooleanField(default=True)
    quota_key = models.CharField(
        max_length=48,
        unique=True,
        editable=False,
    )
    limit_class = models.ForeignKey(
        ProjectLimitClass,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name='projects',
    )
    administrators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='administered_projects',
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='ProjectMembership',
        related_name='projects',
    )
    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk:
            previous_key = type(self).objects.filter(pk=self.pk).values_list('quota_key', flat=True).first()
            if previous_key and self.quota_key != previous_key:
                raise ValidationError({'quota_key': 'A project quota key is immutable.'})
        elif not self.quota_key:
            self.quota_key = generate_project_quota_key()
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.end_date is None or self.end_date >= timezone.localdate()

    def quota_limit_for_model(self, model_name):
        if self.limit_class_id is None:
            return None
        return self.limit_class.model_limits.filter(model_name=model_name).values_list(
            'daily_token_limit', flat=True,
        ).first()

    def set_join_code(self, join_code):
        self.join_code_hash = self.hash_join_code(join_code) if join_code else None

    def check_join_code(self, join_code):
        return bool(self.join_code_hash and join_code and self.join_code_hash == self.hash_join_code(join_code))

    def join_user(self, user):
        status = MEMBERSHIP_REQUESTED if self.join_requires_approval else MEMBERSHIP_ACTIVE
        membership, _ = ProjectMembership.objects.get_or_create(
            user=user,
            project=self,
            defaults={'status': status},
        )
        if not self.join_requires_approval and membership.status != MEMBERSHIP_ACTIVE:
            membership.status = MEMBERSHIP_ACTIVE
            membership.save(update_fields=['status'])
        return membership

    def is_administered_by(self, user):
        return self.administrators.filter(pk=user.pk).exists()

    @staticmethod
    def hash_join_code(join_code):
        return hmac.digest(
            settings.SECRET_KEY.encode(),
            join_code.encode(),
            'sha256',
        ).hex()

    @classmethod
    def active_projects(cls):
        return cls.objects.filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=timezone.localdate()))

    @classmethod
    def joinable_projects(cls):
        return cls.active_projects().filter(join_code_hash__isnull=False)

    @classmethod
    def find_by_join_code(cls, join_code):
        if not join_code:
            return None
        return cls.joinable_projects().filter(join_code_hash=cls.hash_join_code(join_code)).first()

    @classmethod
    def join_user_by_code(cls, user, join_code):
        project = cls.find_by_join_code(join_code)
        return project.join_user(user) if project else None

    @classmethod
    def token_projects_for_user(cls, user):
        if user.is_superuser:
            return cls.active_projects()
        return cls.active_projects().filter(
            projectmembership__user=user,
            projectmembership__status=MEMBERSHIP_ACTIVE,
        )

    @classmethod
    def administered_by(cls, user):
        return cls.objects.filter(administrators=user)

    @classmethod
    def is_project_admin(cls, user):
        return (
            user.is_authenticated
            and user.is_active
            and cls.administered_by(user).exists()
        )

    @classmethod
    def can_use_admin(cls, user):
        return user.is_superuser or cls.is_project_admin(user)

    @classmethod
    def can_administer(cls, user, project):
        return user.is_superuser or (project is not None and project.is_administered_by(user))


class ProjectMembership(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20,
        choices=MEMBERSHIP_STATUS_CHOICES,
        default=MEMBERSHIP_REQUESTED,
    )
    class Meta:
        ordering = ['project', 'user']
        constraints = [
            models.UniqueConstraint(fields=['project', 'user'], name='unique_project_membership'),
        ]

    def __str__(self):
        return f'{self.project}: {self.user}'

    @property
    def allows_token_creation(self):
        return self.status == MEMBERSHIP_ACTIVE and self.project.is_active

    @property
    def active_tokens(self):
        return StaticToken.active_for_user(self.user).filter(project=self.project)


class ProjectModelLimit(models.Model):
    limit_class = models.ForeignKey(
        ProjectLimitClass,
        on_delete=models.CASCADE,
        related_name='model_limits',
    )
    model_name = models.CharField(max_length=200)
    daily_token_limit = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    max_parallel_requests = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
        help_text=(
            'Maximum number of requests Envoy may send to this model at once, '
            'counted separately for each Envoy pod.'
        ),
    )
    class Meta:
        ordering = ['model_name']
        constraints = [
            models.UniqueConstraint(
                fields=['limit_class', 'model_name'],
                name='unique_project_limit_class_model',
            ),
            models.CheckConstraint(
                condition=models.Q(daily_token_limit__gt=0),
                name='project_model_daily_limit_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(max_parallel_requests__isnull=True) | models.Q(max_parallel_requests__gt=0),
                name='project_model_parallel_limit_positive',
            ),
        ]

    def __str__(self):
        return f'{self.limit_class}: {self.model_name} ({self.daily_token_limit}/day)'


class StaticToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='static_tokens',
    )
    name = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64, unique=True)
    token_suffix = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    allowed_count = models.PositiveBigIntegerField(default=0)
    denied_count = models.PositiveBigIntegerField(default=0)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='tokens',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Bearer token'
        verbose_name_plural = 'Bearer tokens'

    @staticmethod
    def hash_token(raw_token):
        return hmac.digest(
            settings.SECRET_KEY.encode(),
            raw_token.encode(),
            'sha256',
        ).hex()

    @property
    def token_indicator(self):
        return f'{TOKEN_PREFIX}...{self.token_suffix}'

    @classmethod
    def create_token(cls, user, name, lifetime_days, project):
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_SECRET_BYTES)
        token = cls.objects.create(
            user=user,
            name=name,
            token_hash=cls.hash_token(raw_token),
            token_suffix=raw_token[-3:],
            expires_at=timezone.now() + timedelta(days=lifetime_days),
            project=project,
        )
        return token, raw_token

    @classmethod
    def active_filter(cls):
        return (
            models.Q(expires_at__gt=timezone.now())
            & (models.Q(project__end_date__isnull=True) | models.Q(project__end_date__gte=timezone.localdate()))
        )

    @classmethod
    def find_valid(cls, raw_token):
        return (
            cls.objects
            .filter(cls.active_filter(), token_hash=cls.hash_token(raw_token))
            .select_related('user', 'project', 'project__limit_class')
            .first()
        )

    @classmethod
    def active_for_user(cls, user):
        return cls.objects.filter(cls.active_filter(), user=user).select_related('project')

    def record_allowed(self):
        StaticToken.objects.filter(pk=self.pk).update(
            allowed_count=models.F('allowed_count') + 1,
        )

    def record_denied(self):
        StaticToken.objects.filter(pk=self.pk).update(
            denied_count=models.F('denied_count') + 1,
        )
