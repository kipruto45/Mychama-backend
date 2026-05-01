# Generated manually for RBAC catalog support.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


ROLE_DEFINITIONS = {
    "super_admin": {
        "name": "Super Admin",
        "description": "Global platform super administrator.",
        "scope": "global",
        "membership_role_key": "SUPERADMIN",
        "sort_order": 10,
    },
    "admin": {
        "name": "Admin",
        "description": "Administrative role with broad governance powers.",
        "scope": "chama",
        "membership_role_key": "ADMIN",
        "sort_order": 20,
    },
    "chairperson": {
        "name": "Chairperson",
        "description": "Primary chama administrator and governance lead.",
        "scope": "chama",
        "membership_role_key": "CHAMA_ADMIN",
        "sort_order": 30,
    },
    "treasurer": {
        "name": "Treasurer",
        "description": "Finance owner for collection, reconciliation, and balances.",
        "scope": "chama",
        "membership_role_key": "TREASURER",
        "sort_order": 40,
    },
    "secretary": {
        "name": "Secretary",
        "description": "Meeting and membership workflow owner.",
        "scope": "chama",
        "membership_role_key": "SECRETARY",
        "sort_order": 50,
    },
    "auditor": {
        "name": "Auditor",
        "description": "Read-focused oversight role for reports and controls.",
        "scope": "chama",
        "membership_role_key": "AUDITOR",
        "sort_order": 60,
    },
    "member": {
        "name": "Member",
        "description": "Standard chama member with basic visibility and participation.",
        "scope": "chama",
        "membership_role_key": "MEMBER",
        "sort_order": 70,
    },
}

PERMISSION_DEFINITIONS = {
    "can_view_chama": {
        "name": "View chama",
        "description": "View chama information and membership-scoped records.",
        "scope": "chama",
        "is_sensitive": False,
    },
    "can_edit_chama": {
        "name": "Edit chama",
        "description": "Edit chama profile, settings, and governance configuration.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_delete_chama": {
        "name": "Delete chama",
        "description": "Delete or archive a chama.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_invite_members": {
        "name": "Invite members",
        "description": "Invite new members into a chama.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_remove_members": {
        "name": "Remove members",
        "description": "Suspend, remove, or reject chama members.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_assign_roles": {
        "name": "Assign roles",
        "description": "Assign and revoke chama role assignments.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_view_finance": {
        "name": "View finance",
        "description": "View finance summaries, ledgers, and balances.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_manage_finance": {
        "name": "Manage finance",
        "description": "Create, update, and reconcile financial records.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_record_payments": {
        "name": "Record payments",
        "description": "Initiate, confirm, or reconcile contribution and payment records.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_view_meetings": {
        "name": "View meetings",
        "description": "View meetings, agendas, minutes, and attendance.",
        "scope": "chama",
        "is_sensitive": False,
    },
    "can_manage_meetings": {
        "name": "Manage meetings",
        "description": "Create and manage meetings, agendas, and minutes.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_view_reports": {
        "name": "View reports",
        "description": "View operational, finance, and audit reports.",
        "scope": "chama",
        "is_sensitive": True,
    },
    "can_manage_notifications": {
        "name": "Manage notifications",
        "description": "Trigger or manage notifications and communication workflows.",
        "scope": "chama",
        "is_sensitive": False,
    },
    "can_use_ai": {
        "name": "Use AI",
        "description": "Use AI-assisted features within the chama context.",
        "scope": "chama",
        "is_sensitive": False,
    },
    "can_export_data": {
        "name": "Export data",
        "description": "Export audit, finance, and operational data.",
        "scope": "chama",
        "is_sensitive": True,
    },
}

ROLE_PERMISSION_MATRIX = {
    "super_admin": set(PERMISSION_DEFINITIONS.keys()),
    "admin": {
        "can_view_chama",
        "can_edit_chama",
        "can_delete_chama",
        "can_invite_members",
        "can_remove_members",
        "can_assign_roles",
        "can_view_finance",
        "can_manage_finance",
        "can_record_payments",
        "can_view_meetings",
        "can_manage_meetings",
        "can_view_reports",
        "can_manage_notifications",
        "can_use_ai",
        "can_export_data",
    },
    "chairperson": {
        "can_view_chama",
        "can_edit_chama",
        "can_delete_chama",
        "can_invite_members",
        "can_remove_members",
        "can_assign_roles",
        "can_view_finance",
        "can_manage_finance",
        "can_record_payments",
        "can_view_meetings",
        "can_manage_meetings",
        "can_view_reports",
        "can_manage_notifications",
        "can_use_ai",
        "can_export_data",
    },
    "treasurer": {
        "can_view_chama",
        "can_view_finance",
        "can_manage_finance",
        "can_record_payments",
        "can_view_meetings",
        "can_view_reports",
        "can_use_ai",
        "can_export_data",
    },
    "secretary": {
        "can_view_chama",
        "can_invite_members",
        "can_view_meetings",
        "can_manage_meetings",
        "can_manage_notifications",
        "can_use_ai",
    },
    "auditor": {
        "can_view_chama",
        "can_view_finance",
        "can_view_meetings",
        "can_view_reports",
        "can_export_data",
    },
    "member": {
        "can_view_chama",
        "can_view_meetings",
        "can_use_ai",
    },
}


def seed_rbac(apps, schema_editor):
    Role = apps.get_model("security", "Role")
    Permission = apps.get_model("security", "Permission")
    RolePermission = apps.get_model("security", "RolePermission")

    permission_by_code = {}
    for code, definition in PERMISSION_DEFINITIONS.items():
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "name": definition["name"],
                "description": definition["description"],
                "scope": definition["scope"],
                "is_system": True,
                "is_sensitive": definition["is_sensitive"],
            },
        )
        permission_by_code[code] = permission

    for code, definition in ROLE_DEFINITIONS.items():
        role, _ = Role.objects.update_or_create(
            code=code,
            defaults={
                "name": definition["name"],
                "description": definition["description"],
                "scope": definition["scope"],
                "membership_role_key": definition["membership_role_key"],
                "is_system": True,
                "sort_order": definition["sort_order"],
            },
        )
        for permission_code in ROLE_PERMISSION_MATRIX.get(code, set()):
            RolePermission.objects.update_or_create(
                role=role,
                permission=permission_by_code[permission_code],
            )


def unseed_rbac(apps, schema_editor):
    RolePermission = apps.get_model("security", "RolePermission")
    Permission = apps.get_model("security", "Permission")
    Role = apps.get_model("security", "Role")

    RolePermission.objects.all().delete()
    Permission.objects.filter(code__in=list(PERMISSION_DEFINITIONS.keys())).delete()
    Role.objects.filter(code__in=list(ROLE_DEFINITIONS.keys())).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("security", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Permission",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("code", models.CharField(db_index=True, max_length=64, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                (
                    "scope",
                    models.CharField(
                        choices=[("global", "Global"), ("chama", "Chama")],
                        default="chama",
                        max_length=16,
                    ),
                ),
                ("is_system", models.BooleanField(db_index=True, default=True)),
                ("is_sensitive", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "indexes": [
                    models.Index(
                        fields=["scope", "is_system"],
                        name="security_pe_scope_9c8189_idx",
                    ),
                    models.Index(
                        fields=["is_sensitive"],
                        name="security_pe_is_sen_b55565_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Role",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                ("code", models.CharField(db_index=True, max_length=64, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("description", models.TextField(blank=True)),
                (
                    "scope",
                    models.CharField(
                        choices=[("global", "Global"), ("chama", "Chama")],
                        default="chama",
                        max_length=16,
                    ),
                ),
                (
                    "membership_role_key",
                    models.CharField(blank=True, db_index=True, max_length=32),
                ),
                ("is_system", models.BooleanField(db_index=True, default=True)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "name"],
                "indexes": [
                    models.Index(
                        fields=["scope", "is_system"],
                        name="security_ro_scope_e52822_idx",
                    ),
                    models.Index(
                        fields=["membership_role_key"],
                        name="security_ro_members_914359_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(app_label)s_%(class)s_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "permission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="permission_roles",
                        to="security.permission",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="security.role",
                    ),
                ),
            ],
            options={
                "ordering": ["role__sort_order", "permission__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="rolepermission",
            constraint=models.UniqueConstraint(
                fields=("role", "permission"),
                name="uniq_role_permission_assignment",
            ),
        ),
        migrations.AddIndex(
            model_name="rolepermission",
            index=models.Index(
                fields=["role", "permission"],
                name="security_ro_role_id_1f0a5b_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="rolepermission",
            index=models.Index(
                fields=["permission", "role"],
                name="security_ro_permis_0d777d_idx",
            ),
        ),
        migrations.RunPython(seed_rbac, unseed_rbac),
    ]
