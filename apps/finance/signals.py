from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.finance.models import InstallmentSchedule, LedgerEntry, Loan, Penalty
from apps.finance.summary import (
    apply_ledger_entry_to_snapshot,
    refresh_snapshot_derived_metrics,
)


@receiver(post_save, sender=LedgerEntry)
def finance_snapshot_on_ledger_post(sender, instance, created, **kwargs):
    if not created:
        return
    apply_ledger_entry_to_snapshot(instance)


@receiver(post_save, sender=Loan)
@receiver(post_delete, sender=Loan)
def finance_snapshot_on_loan_change(sender, instance, **kwargs):
    refresh_snapshot_derived_metrics(instance.chama_id)


@receiver(post_save, sender=InstallmentSchedule)
@receiver(post_delete, sender=InstallmentSchedule)
def finance_snapshot_on_installment_change(sender, instance, **kwargs):
    refresh_snapshot_derived_metrics(instance.loan.chama_id)


@receiver(post_save, sender=Penalty)
@receiver(post_delete, sender=Penalty)
def finance_snapshot_on_penalty_change(sender, instance, **kwargs):
    refresh_snapshot_derived_metrics(instance.chama_id)
