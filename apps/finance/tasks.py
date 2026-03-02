from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.db.models import Count, DecimalField, F, Max, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.chama.models import Chama, ChamaStatus, MemberStatus, Membership, MembershipRole
from apps.finance.models import (
    Contribution,
    LedgerDirection,
    LedgerEntry,
    InstallmentSchedule,
    InstallmentStatus,
    Loan,
    LoanStatus,
    Penalty,
    PenaltyStatus,
)
from apps.finance.services import FinanceService
from apps.meetings.models import Attendance, AttendanceStatus
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from core.algorithms.finance import classify_delinquency, compute_par_ratio
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


def _run_with_job_log(*, name: str, schedule: str, description: str, callback):
    from apps.automations.services import AutomationJobRunner

    return AutomationJobRunner.run_job(
        name=name,
        schedule=schedule,
        description=description,
        callback=callback,
        actor=None,
    )


def _money(value) -> Decimal:
    return Decimal(str(value or "0.00")).quantize(Decimal("0.01"))


@shared_task
def on_membership_approved(user_id: str, chama_id: str):
    def callback():
        membership = Membership.objects.select_related("user", "chama").filter(
            user_id=user_id,
            chama_id=chama_id,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        ).first()
        if not membership:
            return {"status": "skipped", "reason": "membership_not_active"}

        key = f"membership-approved-welcome:{membership.chama_id}:{membership.user_id}"
        NotificationService.send_notification(
            user=membership.user,
            chama=membership.chama,
            channels=["sms", "email"],
            message=(
                "Welcome to Digital Chama. Your membership is approved and you can now deposit using M-Pesa."
            ),
            subject="Membership approved",
            notification_type=NotificationType.SYSTEM,
            idempotency_key=key,
        )
        return {
            "status": "sent",
            "user_id": str(membership.user_id),
            "chama_id": str(membership.chama_id),
        }

    return _run_with_job_log(
        name="on_membership_approved",
        schedule="event",
        description="Sends membership approval welcome notification.",
        callback=callback,
    )


@shared_task
def on_membership_approved_sweep():
    def callback():
        since = timezone.now() - timedelta(days=7)
        sent = 0
        for membership in Membership.objects.select_related("user", "chama").filter(
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            approved_at__gte=since,
        ):
            result = on_membership_approved(
                str(membership.user_id),
                str(membership.chama_id),
            )
            if str(result.get("status", "")).lower() in {"success", "sent"}:
                sent += 1
        return {"sent": sent}

    return _run_with_job_log(
        name="on_membership_approved_sweep",
        schedule="*/30 * * * *",
        description="Backfills welcome notifications for newly approved members.",
        callback=callback,
    )


@shared_task
def contributions_daily_reminder():
    from apps.notifications.tasks import daily_due_reminders

    return _run_with_job_log(
        name="contributions_daily_reminder",
        schedule="0 18 * * *",
        description="Daily contribution reminders.",
        callback=daily_due_reminders,
    )


@shared_task
def contributions_mark_overdue_and_penalize():
    def callback():
        today = timezone.localdate()
        overdue_updated = InstallmentSchedule.objects.filter(
            status=InstallmentStatus.DUE,
            due_date__lt=today,
        ).update(status=InstallmentStatus.OVERDUE)

        penalties_created = 0
        for installment in InstallmentSchedule.objects.select_related("loan", "loan__chama", "loan__member").filter(
            status=InstallmentStatus.OVERDUE,
            loan__status__in=[LoanStatus.APPROVED, LoanStatus.DISBURSED, LoanStatus.ACTIVE],
        ):
            reason = f"Overdue installment penalty (installment {installment.id})"
            from apps.finance.models import Penalty

            exists = Penalty.objects.filter(
                chama_id=installment.loan.chama_id,
                member_id=installment.loan.member_id,
                reason=reason,
                status=PenaltyStatus.UNPAID,
            ).exists()
            if exists:
                continue

            actor = (
                Membership.objects.select_related("user")
                .filter(
                    chama=installment.loan.chama,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
                )
                .first()
            )
            if not actor:
                continue

            amount = installment.loan.late_penalty_value or Decimal("0.00")
            if amount <= Decimal("0.00"):
                continue

            FinanceService.issue_penalty(
                payload={
                    "chama_id": installment.loan.chama_id,
                    "member_id": installment.loan.member_id,
                    "amount": amount,
                    "reason": reason,
                    "due_date": today,
                    "idempotency_key": f"auto-penalty:{installment.id}:{today.isoformat()}",
                },
                actor=actor.user,
            )
            penalties_created += 1

        return {
            "overdue_updated": overdue_updated,
            "penalties_created": penalties_created,
        }

    return _run_with_job_log(
        name="contributions_mark_overdue_and_penalize",
        schedule="0 20 * * *",
        description="Marks overdue obligations and raises penalties.",
        callback=callback,
    )


@shared_task
def contributions_monthly_statement():
    def callback():
        today = timezone.localdate()
        from_date = today.replace(day=1)
        sent = 0

        memberships = Membership.objects.select_related("user", "chama").filter(
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
        )
        for membership in memberships:
            statement = FinanceService.compute_member_statement(
                chama_id=membership.chama_id,
                member_id=membership.user_id,
                from_date=from_date,
                to_date=today,
            )
            msg = (
                "Monthly summary ready. "
                f"Contributions: KES {statement['totals']['contributions']}. "
                f"Repayments: KES {statement['totals']['repayments']}."
            )
            NotificationService.send_notification(
                user=membership.user,
                chama=membership.chama,
                channels=["email"],
                message=msg,
                subject="Monthly statement",
                notification_type=NotificationType.SYSTEM,
                idempotency_key=(
                    f"monthly-statement:{membership.chama_id}:{membership.user_id}:{today:%Y-%m}"
                ),
            )
            sent += 1

        return {"sent": sent, "period_start": from_date.isoformat(), "period_end": today.isoformat()}

    return _run_with_job_log(
        name="contributions_monthly_statement",
        schedule="0 7 1 * *",
        description="Sends monthly statement summaries.",
        callback=callback,
    )


@shared_task
def loans_generate_repayment_schedules(loan_id: str):
    def callback():
        loan = Loan.objects.filter(id=loan_id).first()
        if not loan:
            return {"status": "not_found", "loan_id": loan_id}
        created = FinanceService.generate_schedule(loan)
        return {"loan_id": str(loan.id), "installments_created": created}

    return _run_with_job_log(
        name="loans_generate_repayment_schedules",
        schedule="event",
        description="Generates loan installment schedules post-disbursement.",
        callback=callback,
    )


@shared_task
def loans_due_soon_reminder():
    from apps.notifications.tasks import daily_due_reminders

    return _run_with_job_log(
        name="loans_due_soon_reminder",
        schedule="0 8 * * *",
        description="Sends reminders for installments due soon.",
        callback=daily_due_reminders,
    )


@shared_task
def loans_due_today_reminder():
    from apps.notifications.tasks import daily_due_reminders

    return _run_with_job_log(
        name="loans_due_today_reminder",
        schedule="0 7 * * *",
        description="Sends reminders for installments due today.",
        callback=daily_due_reminders,
    )


@shared_task
def loans_overdue_escalation():
    from apps.notifications.tasks import daily_due_reminders

    return _run_with_job_log(
        name="loans_overdue_escalation",
        schedule="0 9 * * *",
        description="Escalates overdue installments to admins.",
        callback=daily_due_reminders,
    )


@shared_task
def loans_auto_close_when_paid():
    def callback():
        cleared = 0
        candidates = Loan.objects.filter(status__in=[LoanStatus.DISBURSED, LoanStatus.ACTIVE])
        for loan in candidates:
            total_repaid = loan.repayments.aggregate(
                total=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0.00"), output_field=DecimalField()),
                )
            )["total"]
            if total_repaid >= loan.principal:
                loan.status = LoanStatus.PAID
                loan.save(update_fields=["status", "updated_at"])
                cleared += 1
        return {"cleared": cleared}

    return _run_with_job_log(
        name="loans_auto_close_when_paid",
        schedule="0 * * * *",
        description="Automatically marks fully repaid loans as cleared.",
        callback=callback,
    )


@shared_task
def ledger_daily_integrity_audit(chama_id: str | None = None):
    def callback():
        chama_qs = Chama.objects.filter(status=ChamaStatus.ACTIVE)
        if chama_id:
            chama_qs = chama_qs.filter(id=chama_id)

        reviewed = 0
        flagged = 0
        summaries = []
        for chama in chama_qs:
            reviewed += 1
            ledger_qs = LedgerEntry.objects.filter(chama=chama)
            credits = _money(
                ledger_qs.filter(direction=LedgerDirection.CREDIT).aggregate(
                    total=Coalesce(
                        Sum("amount"),
                        Value(Decimal("0.00"), output_field=DecimalField()),
                    )
                )["total"]
            )
            debits = _money(
                ledger_qs.filter(direction=LedgerDirection.DEBIT).aggregate(
                    total=Coalesce(
                        Sum("amount"),
                        Value(Decimal("0.00"), output_field=DecimalField()),
                    )
                )["total"]
            )
            recalculated_balance = _money(credits - debits)

            dashboard = FinanceService.compute_chama_dashboard(chama.id)
            dashboard_balance = _money(dashboard.get("net_position"))
            balance_mismatch = recalculated_balance != dashboard_balance

            reversal_mismatch_count = (
                LedgerEntry.objects.select_related("reversal_of")
                .filter(chama=chama, reversal_of__isnull=False)
                .exclude(
                    amount=F("reversal_of__amount"),
                )
                .count()
            )
            reversal_direction_mismatch = (
                LedgerEntry.objects.select_related("reversal_of")
                .filter(
                    chama=chama,
                    reversal_of__isnull=False,
                    direction=F("reversal_of__direction"),
                )
                .count()
            )
            duplicate_idempotency = list(
                ledger_qs.values("idempotency_key")
                .annotate(total=Count("id"))
                .filter(total__gt=1)
                .values_list("idempotency_key", flat=True)[:50]
            )

            has_issue = (
                balance_mismatch
                or reversal_mismatch_count > 0
                or reversal_direction_mismatch > 0
                or bool(duplicate_idempotency)
            )
            if has_issue:
                flagged += 1
                create_audit_log(
                    actor=None,
                    chama_id=chama.id,
                    action="ledger_integrity_flagged",
                    entity_type="Chama",
                    entity_id=chama.id,
                    metadata={
                        "balance_mismatch": balance_mismatch,
                        "recalculated_balance": str(recalculated_balance),
                        "dashboard_balance": str(dashboard_balance),
                        "reversal_amount_mismatch_count": reversal_mismatch_count,
                        "reversal_direction_mismatch_count": reversal_direction_mismatch,
                        "duplicate_idempotency_keys": duplicate_idempotency,
                    },
                )

            summaries.append(
                {
                    "chama_id": str(chama.id),
                    "ledger_entries": ledger_qs.count(),
                    "credits": str(credits),
                    "debits": str(debits),
                    "recalculated_balance": str(recalculated_balance),
                    "dashboard_balance": str(dashboard_balance),
                    "balance_mismatch": balance_mismatch,
                    "reversal_amount_mismatch_count": reversal_mismatch_count,
                    "reversal_direction_mismatch_count": reversal_direction_mismatch,
                    "duplicate_idempotency_count": len(duplicate_idempotency),
                }
            )

        return {
            "reviewed_chamas": reviewed,
            "flagged_chamas": flagged,
            "summaries": summaries,
            "run_at": timezone.now().isoformat(),
        }

    return _run_with_job_log(
        name="ledger_daily_integrity_audit",
        schedule="0 2 * * *",
        description="Daily ledger integrity checks (balance recompute, reversals, idempotency collisions).",
        callback=callback,
    )


@shared_task
def loans_delinquency_monitor(chama_id: str | None = None):
    def callback():
        today = timezone.localdate()
        loan_qs = Loan.objects.select_related("chama", "member").filter(
            status__in=[
                LoanStatus.APPROVED,
                LoanStatus.DISBURSING,
                LoanStatus.DISBURSED,
                LoanStatus.ACTIVE,
            ],
            chama__status=ChamaStatus.ACTIVE,
        )
        if chama_id:
            loan_qs = loan_qs.filter(chama_id=chama_id)

        buckets = {
            "current": {"count": 0, "outstanding": Decimal("0.00")},
            "dpd_1_30": {"count": 0, "outstanding": Decimal("0.00")},
            "dpd_31_60": {"count": 0, "outstanding": Decimal("0.00")},
            "dpd_61_90": {"count": 0, "outstanding": Decimal("0.00")},
            "dpd_90_plus": {"count": 0, "outstanding": Decimal("0.00")},
        }
        par_rows = []
        reminders = 0
        escalations = 0

        for loan in loan_qs:
            overdue_installments = loan.installments.filter(
                status=InstallmentStatus.OVERDUE
            )
            if overdue_installments.exists():
                earliest_due = overdue_installments.order_by("due_date").first()
                days_past_due = max((today - earliest_due.due_date).days, 0) if earliest_due else 0
            else:
                days_past_due = 0

            total_repaid = _money(
                loan.repayments.aggregate(
                    total=Coalesce(
                        Sum("amount"),
                        Value(Decimal("0.00"), output_field=DecimalField()),
                    )
                )["total"]
            )
            outstanding = _money(max(loan.principal - total_repaid, Decimal("0.00")))
            bucket = classify_delinquency(days_past_due)
            buckets.setdefault(bucket, {"count": 0, "outstanding": Decimal("0.00")})
            buckets[bucket]["count"] += 1
            buckets[bucket]["outstanding"] = _money(
                buckets[bucket]["outstanding"] + outstanding
            )
            par_rows.append(
                {"outstanding": outstanding, "days_past_due": days_past_due}
            )

            if days_past_due >= 1:
                NotificationService.send_notification(
                    user=loan.member,
                    chama=loan.chama,
                    channels=["sms", "in_app"],
                    message=(
                        "Loan reminder: you have overdue installments. "
                        "Please repay to avoid penalties."
                    ),
                    subject="Loan overdue reminder",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=f"loan-dpd-reminder:{loan.id}:{today.isoformat()}",
                )
                reminders += 1

            if days_past_due < 31:
                continue

            admins = Membership.objects.select_related("user").filter(
                chama=loan.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.TREASURER,
                    MembershipRole.SECRETARY,
                ],
            )
            for admin in admins:
                NotificationService.send_notification(
                    user=admin.user,
                    chama=loan.chama,
                    channels=["in_app", "email"],
                    message=(
                        f"Delinquency alert: member {loan.member.full_name} has "
                        f"loan {loan.id} at {days_past_due} DPD."
                    ),
                    subject="Loan delinquency escalation",
                    notification_type=NotificationType.LOAN_UPDATE,
                    idempotency_key=(
                        f"loan-dpd-escalation:{loan.id}:{admin.user_id}:{today.isoformat()}"
                    ),
                )
                escalations += 1

        par30 = compute_par_ratio(loans=par_rows, days_threshold=30)
        par90 = compute_par_ratio(loans=par_rows, days_threshold=90)

        return {
            "run_date": today.isoformat(),
            "par30_percent": str(par30),
            "par90_percent": str(par90),
            "bucket_summary": {
                key: {
                    "count": value["count"],
                    "outstanding": str(_money(value["outstanding"])),
                }
                for key, value in buckets.items()
            },
            "reminders_sent": reminders,
            "escalations_sent": escalations,
            "loans_reviewed": len(par_rows),
        }

    return _run_with_job_log(
        name="loans_delinquency_monitor",
        schedule="0 9 * * *",
        description="Computes DPD buckets and PAR metrics, then sends delinquency reminders/escalations.",
        callback=callback,
    )


@shared_task
def loans_auto_penalty_calculator(chama_id: str | None = None):
    def callback():
        today = timezone.localdate()
        penalty_cap_percent = Decimal(
            str(
                getattr(
                    settings,
                    "LOAN_AUTO_PENALTY_CAP_PERCENT",
                    10,
                )
            )
        )
        by_tier = {
            "dpd_1_30": Decimal("0.01"),
            "dpd_31_60": Decimal("0.02"),
            "dpd_61_90": Decimal("0.03"),
            "dpd_90_plus": Decimal("0.05"),
        }

        loans = Loan.objects.select_related("chama", "member").filter(
            status__in=[LoanStatus.DISBURSED, LoanStatus.ACTIVE],
            installments__status=InstallmentStatus.OVERDUE,
            chama__status=ChamaStatus.ACTIVE,
        ).distinct()
        if chama_id:
            loans = loans.filter(chama_id=chama_id)

        created = 0
        skipped = 0
        for loan in loans:
            overdue_due_date = (
                loan.installments.filter(status=InstallmentStatus.OVERDUE)
                .order_by("due_date")
                .values_list("due_date", flat=True)
                .first()
            )
            if not overdue_due_date:
                skipped += 1
                continue

            dpd = max((today - overdue_due_date).days, 0)
            bucket = classify_delinquency(dpd)
            rate = by_tier.get(bucket)
            if not rate:
                skipped += 1
                continue

            total_repaid = _money(
                loan.repayments.aggregate(
                    total=Coalesce(
                        Sum("amount"),
                        Value(Decimal("0.00"), output_field=DecimalField()),
                    )
                )["total"]
            )
            outstanding = _money(max(loan.principal - total_repaid, Decimal("0.00")))
            if outstanding <= Decimal("0.00"):
                skipped += 1
                continue

            cap_amount = _money(loan.principal * (penalty_cap_percent / Decimal("100")))
            existing_auto_penalties = _money(
                Penalty.objects.filter(
                    chama=loan.chama,
                    member=loan.member,
                    reason__icontains=f"loan:{loan.id}",
                    status=PenaltyStatus.UNPAID,
                ).aggregate(
                    total=Coalesce(
                        Sum("amount"),
                        Value(Decimal("0.00"), output_field=DecimalField()),
                    )
                )["total"]
            )
            remaining_cap = _money(max(cap_amount - existing_auto_penalties, Decimal("0.00")))
            if remaining_cap <= Decimal("0.00"):
                skipped += 1
                continue

            proposed_penalty = _money(outstanding * rate)
            penalty_amount = _money(min(proposed_penalty, remaining_cap))
            if penalty_amount <= Decimal("0.00"):
                skipped += 1
                continue

            actor_membership = Membership.objects.select_related("user").filter(
                chama=loan.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
            ).first()
            if not actor_membership:
                skipped += 1
                continue

            try:
                FinanceService.issue_penalty(
                    payload={
                        "chama_id": loan.chama_id,
                        "member_id": loan.member_id,
                        "amount": penalty_amount,
                        "reason": (
                            f"Auto delinquency penalty loan:{loan.id} "
                            f"bucket:{bucket} dpd:{dpd}"
                        ),
                        "due_date": today,
                        "idempotency_key": (
                            f"loan-auto-penalty:{loan.id}:{today.isoformat()}"
                        ),
                    },
                    actor=actor_membership.user,
                )
                created += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed auto penalty calculation for loan=%s", loan.id)
                skipped += 1

        create_audit_log(
            actor=None,
            chama_id=chama_id,
            action="loans_auto_penalty_calculated",
            entity_type="Loan",
            entity_id=None,
            metadata={
                "created": created,
                "skipped": skipped,
                "cap_percent": str(penalty_cap_percent),
            },
        )
        return {
            "created": created,
            "skipped": skipped,
            "run_date": today.isoformat(),
            "cap_percent": str(penalty_cap_percent),
        }

    return _run_with_job_log(
        name="loans_auto_penalty_calculator",
        schedule="0 20 * * *",
        description="Applies tiered overdue penalties with principal-based cap.",
        callback=callback,
    )


@shared_task
def memberships_inactivity_monitor(chama_id: str | None = None):
    def callback():
        today = timezone.localdate()
        contribution_inactive_days = 90
        attendance_inactive_days = 180

        memberships = Membership.objects.select_related("user", "chama").filter(
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            chama__status=ChamaStatus.ACTIVE,
        )
        if chama_id:
            memberships = memberships.filter(chama_id=chama_id)

        flagged = 0
        reviewed = 0
        for membership in memberships:
            reviewed += 1
            last_contribution = Contribution.objects.filter(
                chama=membership.chama,
                member=membership.user,
            ).aggregate(last_paid=Max("date_paid"))["last_paid"]
            last_attendance = Attendance.objects.filter(
                meeting__chama=membership.chama,
                member=membership.user,
                status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
            ).aggregate(last_seen=Max("meeting__date"))["last_seen"]

            no_contribution = (
                not last_contribution
                or (today - last_contribution).days >= contribution_inactive_days
            )
            no_attendance = (
                not last_attendance
                or (timezone.now() - last_attendance).days >= attendance_inactive_days
            )
            if not (no_contribution or no_attendance):
                continue

            flagged += 1
            reviewer_memberships = Membership.objects.select_related("user").filter(
                chama=membership.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[
                    MembershipRole.CHAMA_ADMIN,
                    MembershipRole.SECRETARY,
                ],
            )
            for reviewer in reviewer_memberships:
                NotificationService.send_notification(
                    user=reviewer.user,
                    chama=membership.chama,
                    channels=["in_app", "email"],
                    message=(
                        f"Inactivity review needed for {membership.user.full_name}. "
                        "Member has low contribution/attendance activity."
                    ),
                    subject="Membership inactivity review",
                    notification_type=NotificationType.SYSTEM,
                    idempotency_key=(
                        "membership-inactivity:"
                        f"{membership.chama_id}:{membership.user_id}:{today.isoformat()}:{reviewer.user_id}"
                    ),
                )

        return {
            "reviewed_memberships": reviewed,
            "flagged_memberships": flagged,
            "run_date": today.isoformat(),
            "contribution_threshold_days": contribution_inactive_days,
            "attendance_threshold_days": attendance_inactive_days,
        }

    return _run_with_job_log(
        name="memberships_inactivity_monitor",
        schedule="0 6 * * *",
        description="Flags members with prolonged contribution or attendance inactivity for governance review.",
        callback=callback,
    )
