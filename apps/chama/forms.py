from django import forms

from apps.chama.models import Chama, Invite, Membership, MembershipRole
from core.utils import normalize_kenyan_phone


class ChamaForm(forms.ModelForm):
    class Meta:
        model = Chama
        fields = ("name", "description", "county", "subcounty", "currency", "status")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class MembershipForm(forms.ModelForm):
    class Meta:
        model = Membership
        fields = (
            "user",
            "chama",
            "role",
            "status",
            "exit_reason",
        )


class InviteForm(forms.ModelForm):
    class Meta:
        model = Invite
        fields = ("chama", "identifier", "status", "expires_at", "invited_by")
        widgets = {
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class JoinChamaForm(forms.Form):
    chama_id = forms.UUIDField()
    request_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    invite_token = forms.CharField(required=False, max_length=128)
    join_code = forms.CharField(required=False, max_length=24)


class MembershipReviewActionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            ("approve", "Approve"),
            ("reject", "Reject"),
            ("needs_info", "Needs Info"),
        ]
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class InviteLinkCreateForm(forms.Form):
    expires_in_days = forms.IntegerField(min_value=1, max_value=30, initial=7)
    max_uses = forms.IntegerField(min_value=1, required=False)
    restricted_phone = forms.CharField(max_length=16, required=False)
    preassigned_role = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Default (MEMBER)"),
            (MembershipRole.MEMBER, "Member"),
            (MembershipRole.AUDITOR, "Auditor"),
        ],
    )

    def clean_restricted_phone(self):
        phone = str(self.cleaned_data.get("restricted_phone", "")).strip()
        if not phone:
            return ""
        return normalize_kenyan_phone(phone)
