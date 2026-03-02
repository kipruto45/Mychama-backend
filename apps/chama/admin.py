from django.contrib import admin

from apps.chama.models import (
    Chama,
    ChamaSettings,
    ContributionPlan,
    LoanPolicy,
    ExpensePolicy,
    PaymentProviderConfig,
    Invite,
    InviteLink,
    Membership,
    MembershipRequest,
    RoleDelegation,
)


@admin.register(Chama)
class ChamaAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "chama_type",
        "county",
        "currency",
        "status",
        "allow_public_join",
        "require_approval",
        "setup_completed",
        "created_at",
    )
    list_filter = (
        "status",
        "chama_type",
        "county",
        "currency",
        "allow_public_join",
        "require_approval",
        "setup_completed",
    )
    search_fields = ("name", "county", "subcounty", "join_code")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "description", "chama_type")}),
        (
            "Location",
            {"fields": ("county", "subcounty", "currency")},
        ),
        (
            "Join Settings",
            {
                "fields": (
                    "join_code",
                    "join_code_expires_at",
                    "allow_public_join",
                    "require_approval",
                    "max_members",
                )
            },
        ),
        ("Status", {"fields": ("status", "setup_completed", "setup_step")}),
    )


@admin.register(ChamaSettings)
class ChamaSettingsAdmin(admin.ModelAdmin):
    list_display = ("chama", "join_approval_policy", "meeting_frequency", "voting_quorum_percent")
    list_filter = ("join_approval_policy", "meeting_frequency")
    search_fields = ("chama__name",)


@admin.register(ContributionPlan)
class ContributionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "chama", "contribution_type", "frequency", "fixed_amount", "is_active", "is_default")
    list_filter = ("contribution_type", "frequency", "is_active", "is_default")
    search_fields = ("name", "chama__name")


@admin.register(LoanPolicy)
class LoanPolicyAdmin(admin.ModelAdmin):
    list_display = ("chama", "loans_enabled", "interest_model", "interest_rate", "require_guarantors")
    list_filter = ("loans_enabled", "interest_model", "require_guarantors")


@admin.register(ExpensePolicy)
class ExpensePolicyAdmin(admin.ModelAdmin):
    list_display = ("chama", "allow_withdrawals", "allow_expenses", "treasurer_admin_threshold")
    list_filter = ("allow_withdrawals", "allow_expenses")


@admin.register(PaymentProviderConfig)
class PaymentProviderConfigAdmin(admin.ModelAdmin):
    list_display = ("chama", "provider_type", "is_active", "allow_manual_entry")
    list_filter = ("provider_type", "is_active")
    search_fields = ("chama__name",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "role",
        "status",
        "is_active",
        "is_approved",
        "joined_at",
    )
    list_filter = ("role", "status", "is_active", "is_approved", "chama")
    search_fields = ("user__full_name", "user__phone", "user__email", "chama__name")
    readonly_fields = ("created_at", "updated_at", "joined_at")


@admin.register(MembershipRequest)
class MembershipRequestAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "chama",
        "status",
        "reviewed_by",
        "reviewed_at",
        "expires_at",
        "created_at",
    )
    list_filter = ("status", "chama", "created_at", "reviewed_at")
    search_fields = (
        "user__full_name",
        "user__phone",
        "user__email",
        "chama__name",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("identifier", "chama", "invited_by", "status", "expires_at", "created_at")
    list_filter = ("status", "expires_at", "chama")
    search_fields = ("identifier", "chama__name", "invited_by__phone", "invited_by__email")
    readonly_fields = ("token", "created_at", "updated_at")


@admin.register(InviteLink)
class InviteLinkAdmin(admin.ModelAdmin):
    list_display = (
        "chama",
        "token",
        "preassigned_role",
        "is_active",
        "current_uses",
        "max_uses",
        "expires_at",
        "created_at",
    )
    list_filter = ("is_active", "chama", "created_at")
    search_fields = ("chama__name", "token")
    readonly_fields = ("token", "current_uses", "created_at", "updated_at")


@admin.register(RoleDelegation)
class RoleDelegationAdmin(admin.ModelAdmin):
    list_display = (
        "delegator",
        "delegatee",
        "role",
        "starts_at",
        "ends_at",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = (
        "delegator__phone",
        "delegatee__phone",
        "delegator__full_name",
        "delegatee__full_name",
    )
    readonly_fields = ("created_at", "updated_at")
