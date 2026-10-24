from django import forms
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import path, reverse
from social_django.models import Association, Nonce, UserSocialAuth

from apps.authz.models import (
    Project,
    ProjectLimitClass,
    ProjectMembership,
    ProjectModelLimit,
    StaticToken,
)
from apps.authz.kubernetes import (
    PolicyDeploymentError,
    PolicyDeploymentInProgress,
    deploy_all_policies,
)


admin.site.unregister([Group, User, UserSocialAuth, Nonce, Association])
admin.site.index_template = 'admin/grantdeck_index.html'


ENVOY_POLICY_UPDATE_MESSAGE = (
    'Changes are saved. Please note that the Envoy policies must be updated '
    'to make these changes effective.'
)


class ProjectModelLimitInline(admin.TabularInline):
    model = ProjectModelLimit
    fields = ['model_name', 'daily_token_limit', 'max_parallel_requests']
    extra = 1


class ProjectMembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 0


class ProjectAdminForm(forms.ModelForm):
    join_code = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Set a new project code.',
    )
    clear_join_code = forms.BooleanField(required=False)

    class Meta:
        model = Project
        fields = ['name', 'shortcut', 'end_date', 'administrators', 'limit_class', 'join_requires_approval']

    def clean_join_code(self):
        join_code = self.cleaned_data['join_code']
        if join_code:
            duplicate = Project.objects.filter(join_code_hash=Project.hash_join_code(join_code))
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise forms.ValidationError('Project code already exists.')
        return join_code


class SuperuserOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(User)
class GrantDeckUserAdmin(UserAdmin):
    list_display = (*UserAdmin.list_display, 'project_names')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('projects')

    @admin.display(description='Projects')
    def project_names(self, user):
        return ', '.join(project.name for project in user.projects.all()) or '-'


class ProjectScopedAdmin(admin.ModelAdmin):
    def can_use_project_admin(self, request, obj=None):
        if obj is None:
            return Project.can_use_admin(request.user)
        return Project.can_administer(request.user, self.project_for_object(obj))

    def has_module_permission(self, request):
        return Project.can_use_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return self.can_use_project_admin(request, obj)

    def has_add_permission(self, request):
        return Project.can_use_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return self.can_use_project_admin(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.can_use_project_admin(request, obj)


@admin.register(Project)
class ProjectAdmin(ProjectScopedAdmin):
    form = ProjectAdminForm
    list_display = ['name', 'shortcut', 'limit_class', 'end_date', 'administrators_list', 'token_count']
    search_fields = ['name', 'shortcut']
    filter_horizontal = ['administrators']
    inlines = [ProjectMembershipInline]

    def project_for_object(self, project):
        return project

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .annotate(
                token_count_value=Count('tokens', distinct=True),
            )
            .select_related('limit_class')
        )
        if request.user.is_superuser:
            return queryset
        return queryset.filter(pk__in=Project.administered_by(request.user))

    @admin.display(description='Administrators')
    def administrators_list(self, project):
        administrators = [user.get_username() for user in project.administrators.all()]
        return ', '.join(administrators) or '-'

    @admin.display(description='Bearer tokens')
    def token_count(self, project):
        return project.token_count_value

    def get_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return ['name', 'shortcut', 'end_date', 'join_requires_approval', 'join_code', 'clear_join_code']
        return [
            'name', 'shortcut', 'end_date', 'administrators', 'limit_class',
            'join_requires_approval', 'join_code', 'clear_join_code',
        ]

    def get_inline_instances(self, request, obj=None):
        if request.user.is_superuser:
            return super().get_inline_instances(request, obj)
        return []

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        join_code = form.cleaned_data.get('join_code')
        if join_code:
            obj.set_join_code(join_code)
        elif form.cleaned_data.get('clear_join_code'):
            obj.set_join_code('')
        super().save_model(request, obj, form, change)
        if not change or 'limit_class' in form.changed_data:
            self.message_user(
                request,
                ENVOY_POLICY_UPDATE_MESSAGE,
                level=messages.WARNING,
            )


@admin.register(ProjectLimitClass)
class ProjectLimitClassAdmin(SuperuserOnlyAdmin):
    list_display = ['name', 'slug', 'project_count', 'model_count']
    search_fields = ['name', 'slug']
    inlines = [ProjectModelLimitInline]

    def save_related(self, request, form, formsets, change):
        model_limits_changed = any(formset.has_changed() for formset in formsets)
        super().save_related(request, form, formsets, change)
        if model_limits_changed:
            self.message_user(
                request,
                ENVOY_POLICY_UPDATE_MESSAGE,
                level=messages.WARNING,
            )

    def delete_model(self, request, obj):
        has_model_limits = obj.model_limits.exists()
        super().delete_model(request, obj)
        if has_model_limits:
            self.message_user(
                request,
                ENVOY_POLICY_UPDATE_MESSAGE,
                level=messages.WARNING,
            )

    def delete_queryset(self, request, queryset):
        has_model_limits = ProjectModelLimit.objects.filter(
            limit_class__in=queryset,
        ).exists()
        super().delete_queryset(request, queryset)
        if has_model_limits:
            self.message_user(
                request,
                ENVOY_POLICY_UPDATE_MESSAGE,
                level=messages.WARNING,
            )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            project_count_value=Count('projects', distinct=True),
            model_count_value=Count('model_limits', distinct=True),
        )

    @admin.display(description='Projects')
    def project_count(self, policy_class):
        return policy_class.project_count_value

    @admin.display(description='Models')
    def model_count(self, policy_class):
        return policy_class.model_count_value


@admin.register(ProjectModelLimit)
class ProjectModelLimitAdmin(SuperuserOnlyAdmin):
    list_display = [
        'model_name',
        'limit_class',
        'project_count',
        'daily_token_limit',
        'max_parallel_requests',
    ]
    list_filter = ['limit_class']
    search_fields = ['model_name', 'limit_class__name', 'limit_class__slug']
    autocomplete_fields = ['limit_class']
    list_select_related = ['limit_class']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            project_count_value=Count('limit_class__projects', distinct=True),
        )

    @admin.display(description='Projects', ordering='project_count_value')
    def project_count(self, model_limit):
        return model_limit.project_count_value

    def get_urls(self):
        custom_urls = [
            path(
                'deploy-policies/',
                self.admin_site.admin_view(self.deploy_policies_view),
                name='authz_projectmodellimit_deploy_policies',
            ),
        ]
        return custom_urls + super().get_urls()

    def deploy_policies_view(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])

        try:
            result = deploy_all_policies(actor=request.user.get_username())
        except PolicyDeploymentInProgress as exc:
            self.message_user(request, str(exc), level=messages.INFO)
        except PolicyDeploymentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                (
                    f'Deployed {result.applied} Envoy policy resources in '
                    f'{result.namespace}; pruned {result.pruned} stale resources.'
                ),
                level=messages.SUCCESS,
            )
        return redirect(reverse('admin:authz_projectmodellimit_changelist'))

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self.message_user(
            request,
            ENVOY_POLICY_UPDATE_MESSAGE,
            level=messages.WARNING,
        )

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self.message_user(
            request,
            ENVOY_POLICY_UPDATE_MESSAGE,
            level=messages.WARNING,
        )

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        self.message_user(
            request,
            ENVOY_POLICY_UPDATE_MESSAGE,
            level=messages.WARNING,
        )


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(ProjectScopedAdmin):
    list_display = ['project', 'user', 'status']
    list_filter = ['project', 'status']
    search_fields = ['project__name', 'user__username']

    def project_for_object(self, membership):
        return membership.project

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('project', 'user')
        if request.user.is_superuser:
            return queryset
        return queryset.filter(project__in=Project.administered_by(request.user))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'project' and not request.user.is_superuser:
            kwargs['queryset'] = Project.administered_by(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(StaticToken)
class StaticTokenAdmin(SuperuserOnlyAdmin):
    list_display = ['name', 'token_indicator', 'user', 'project', 'allowed_count', 'denied_count', 'created_at', 'expires_at']
    list_filter = ['project']
    search_fields = ['name', 'user__username']
    readonly_fields = ['token_indicator', 'token_hash', 'created_at', 'allowed_count', 'denied_count']

    def get_fields(self, request, obj=None):
        return [
            'user',
            'name',
            'token_indicator',
            'project',
            'created_at',
            'expires_at',
            'allowed_count',
            'denied_count',
            'token_hash',
        ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'project')

    def has_add_permission(self, request):
        return False
