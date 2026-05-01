from django import forms

from apps.finance.models import Contribution, ContributionType, Loan, Penalty, Repayment


class ContributionTypeForm(forms.ModelForm):
    class Meta:
        model = ContributionType
        fields = ("name", "frequency", "default_amount", "is_active")


class ContributionForm(forms.ModelForm):
    class Meta:
        model = Contribution
        fields = (
            "member",
            "contribution_type",
            "amount",
            "date_paid",
            "method",
            "receipt_code",
        )
        widgets = {
            "date_paid": forms.DateInput(attrs={"type": "date"}),
        }


class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = (
            "member",
            "principal",
            "interest_type",
            "interest_rate",
            "duration_months",
        )


class RepaymentForm(forms.ModelForm):
    class Meta:
        model = Repayment
        fields = ("loan", "amount", "date_paid", "method", "receipt_code")
        widgets = {
            "date_paid": forms.DateInput(attrs={"type": "date"}),
        }


class PenaltyForm(forms.ModelForm):
    class Meta:
        model = Penalty
        fields = ("member", "amount", "reason", "due_date")
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }
