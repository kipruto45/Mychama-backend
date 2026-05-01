from __future__ import annotations

import logging
from datetime import date, timedelta

from celery import shared_task
from django.db.models import Count, Q
from django.utils import timezone

from apps.chama.models import (
    Chama,
    ChamaStatus,
    Membership,
    MembershipRole,
    MemberStatus,
)
from apps.payments.models import (
    MpesaB2CStatus,
    MpesaC2BProcessingStatus,
    MpesaC2BTransaction,
    MpesaSTKTransaction,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentIntentType,
)
from apps.payments.services import MpesaService, PaymentWorkflowService
from apps.security.models import DeviceSession
from core.audit import create_audit_log

logger = logging.getLogger(__name__)


def _notify_b2c_final_failure(intent: PaymentIntent, *, attempts: int, reason: str = "") -> int:
    notified = 0
    memberships = Membership.objects.select_related("user").filter(
        chama=intent.chama,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
    )
    for membership in memberships:
        try:
            from apps.notifications.models import NotificationType
            from apps.notifications.services import NotificationService

            NotificationService.send_notification(
                user=membership.user,
                chama=intent.chama,
                channels=["in_app", "push", "sms"],
                message=(
                    f"M-Pesa payout failed after {attempts} attempts. "
                    f"Intent: {intent.id}. {reason}".strip()
                ),
                subject="Payout failed",
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                idempotency_key=(
                    f"payment:b2c:final-failure:{intent.id}:{membership.user_id}:{attempts}"
                ),
            )
            notified += 1
        except Exception:  # noqa: BLE001
            logger.exception("Failed notifying final B2C failure for intent=%s", intent.id)
    return notified


@shared_task
def reconcile_mpesa_payments(run_date: str | None = None):
    """Backward-compatible legacy reconciliation task."""
    target_date = timezone.localdate()
    if run_date:
        target_date = date.fromisoformat(run_date)

    result = MpesaService.reconcile_successful_callbacks(on_date=target_date)
    logger.info(
        "Legacy M-Pesa reconciliation %s: total=%s matched=%s missing_finance=%s missing_mpesa=%s",
        result.run_date,
        result.total_success_transactions,
        result.matched_transactions,
        len(result.missing_in_finance),
        len(result.missing_in_mpesa),
    )
    return {
        "run_date": result.run_date,
        "total_success_transactions": result.total_success_transactions,
        "matched_transactions": result.matched_transactions,
        "missing_in_finance": result.missing_in_finance,
        "missing_in_mpesa": result.missing_in_mpesa,
    }


@shared_task
def payments_process_stk_callback(payload: dict, source_ip: str | None = None, headers: dict | None = None):
    return PaymentWorkflowService.process_stk_callback(
        payload,
        source_ip=source_ip,
        headers=headers or {},
    )


@shared_task
def payments_process_c2b_confirmation(payload: dict, source_ip: str | None = None, headers: dict | None = None):
    return PaymentWorkflowService.process_c2b_confirmation(
        payload,
        source_ip=source_ip,
        headers=headers or {},
    )


@shared_task
def payments_process_c2b_validation(payload: dict, source_ip: str | None = None, headers: dict | None = None):
    return PaymentWorkflowService.process_c2b_validation(
        payload,
        source_ip=source_ip,
        headers=headers or {},
    )


@shared_task
def payments_expire_pending_stk():
    from apps.automations.services import AutomationJobRunner

    def callback():
        expired = PaymentWorkflowService.expire_pending_stk(now=timezone.now())
        logger.info("Expired %s pending STK intents", expired)
        return {"expired": expired, "run_at": timezone.now().isoformat()}

    return AutomationJobRunner.run_job(
        name="payments_expire_pending_stk",
        schedule="*/5 * * * *",
        description="Expires stale pending STK intents.",
        callback=callback,
    )


@shared_task
def payments_daily_reconciliation(chama_id: str | None = None):
    from apps.automations.services import AutomationJobRunner

    def callback():
        run = PaymentWorkflowService.run_reconciliation(chama_id=chama_id)
        logger.info(
            "Payments reconciliation run=%s status=%s chama=%s",
            run.id,
            run.status,
            run.chama_id,
        )
        return {
            "run_id": str(run.id),
            "status": run.status,
            "totals": run.totals,
            "anomalies": run.anomalies,
        }

    return AutomationJobRunner.run_job(
        name="payments_daily_reconciliation",
        schedule="0 22 * * *",
        description="Runs daily payment-to-ledger reconciliation.",
        callback=callback,
    )


@shared_task
def payments_notify_loan_approved(intent_id: str):
    intent = (
        PaymentIntent.objects.select_related("created_by", "chama")
        .filter(id=intent_id)
        .first()
    )
    if not intent or intent.intent_type != PaymentIntentType.LOAN_DISBURSEMENT:
        return {"status": "ignored", "intent_id": intent_id}
    if not intent.created_by:
        return {"status": "no_user", "intent_id": intent_id}

    try:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        NotificationService.send_notification(
            user=intent.created_by,
            chama=intent.chama,
            channels=["sms", "email"],
            message=(
                "Your loan has been approved and is queued for M-Pesa disbursement. "
                "You will receive a confirmation once sent."
            ),
            subject="Loan approved",
            notification_type=NotificationType.LOAN_UPDATE,
            idempotency_key=f"payment:loan-approved:{intent.id}",
        )
        return {"status": "sent", "intent_id": intent_id}
    except Exception:  # noqa: BLE001
        logger.exception("Failed sending loan approved notification for %s", intent_id)
        return {"status": "failed", "intent_id": intent_id}


@shared_task
def payments_escalate_pending_disbursements():
    from apps.automations.services import AutomationJobRunner

    def callback():
        notified = PaymentWorkflowService.escalate_pending_disbursements(
            now=timezone.now()
        )
        logger.info("Escalated pending disbursement notifications=%s", notified)
        return {"notified": notified, "run_at": timezone.now().isoformat()}

    return AutomationJobRunner.run_job(
        name="payments_escalate_pending_disbursements",
        schedule="0 * * * *",
        description="Escalates loan disbursements that remain pending too long.",
        callback=callback,
    )


@shared_task
def payments_retry_timeouts():
    from apps.automations.services import AutomationJobRunner

    def callback():
        timed_out = (
            PaymentIntent.objects.filter(
                intent_type__in=[
                    PaymentIntentType.WITHDRAWAL,
                    PaymentIntentType.LOAN_DISBURSEMENT,
                ],
                b2c_payouts__status=MpesaB2CStatus.TIMEOUT,
            )
            .distinct()
            .count()
        )
        admin_alerts = 0
        for intent in PaymentIntent.objects.filter(
            intent_type__in=[
                PaymentIntentType.WITHDRAWAL,
                PaymentIntentType.LOAN_DISBURSEMENT,
            ],
            b2c_payouts__status=MpesaB2CStatus.TIMEOUT,
        ).distinct():
            memberships = Membership.objects.select_related("user").filter(
                chama=intent.chama,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
            )
            for membership in memberships:
                try:
                    from apps.notifications.models import NotificationType
                    from apps.notifications.services import NotificationService

                    NotificationService.send_notification(
                        user=membership.user,
                        chama=intent.chama,
                        channels=["email"],
                        message=(
                            "B2C payout timeout requires manual follow-up. "
                            f"Intent: {intent.id}."
                        ),
                        subject="Payout timeout",
                        notification_type=NotificationType.SYSTEM,
                        idempotency_key=(
                            f"payment:timeout-alert:{intent.id}:{membership.user_id}:{timezone.localdate()}"
                        ),
                    )
                    admin_alerts += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed timeout escalation for intent=%s", intent.id
                    )

        return {
            "timeouts": timed_out,
            "admin_alerts": admin_alerts,
            "run_at": timezone.now().isoformat(),
        }

    return AutomationJobRunner.run_job(
        name="payments_retry_timeouts",
        schedule="*/10 * * * *",
        description="Scans B2C timeout payouts and alerts finance admins.",
        callback=callback,
    )


@shared_task
def payments_retry_failed_b2c_payouts():
    from apps.automations.services import AutomationJobRunner

    def callback():
        retried = 0
        escalated = 0
        final_failure_alerts = 0
        intents = PaymentIntent.objects.filter(
            intent_type__in=[
                PaymentIntentType.WITHDRAWAL,
                PaymentIntentType.LOAN_DISBURSEMENT,
            ],
            b2c_payouts__status__in=[MpesaB2CStatus.FAILED, MpesaB2CStatus.TIMEOUT],
        ).distinct()

        for intent in intents:
            attempts = intent.b2c_payouts.count()
            actor_membership = (
                Membership.objects.select_related("user")
                .filter(
                    chama=intent.chama,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
                )
                .order_by("created_at")
                .first()
            )
            if not actor_membership:
                escalated += 1
                final_failure_alerts += _notify_b2c_final_failure(
                    intent,
                    attempts=attempts,
                    reason="No eligible approver found for automated retry.",
                )
                continue
            if attempts >= 3:
                escalated += 1
                final_failure_alerts += _notify_b2c_final_failure(
                    intent,
                    attempts=attempts,
                    reason="Automatic retry limit reached.",
                )
                continue
            try:
                PaymentWorkflowService.send_b2c_payout(
                    intent_id=str(intent.id),
                    actor=actor_membership.user,
                )
                retried += 1
            except Exception:  # noqa: BLE001
                logger.exception("Failed retrying B2C payout for intent=%s", intent.id)
                escalated += 1
                if attempts + 1 >= 3:
                    final_failure_alerts += _notify_b2c_final_failure(
                        intent,
                        attempts=attempts + 1,
                        reason="Automatic retry limit reached after latest failure.",
                    )

        return {
            "retried": retried,
            "escalated": escalated,
            "final_failure_alerts": final_failure_alerts,
        }

    return AutomationJobRunner.run_job(
        name="payments_retry_failed_b2c_payouts",
        schedule="*/15 * * * *",
        description="Retries failed or timed-out B2C payouts up to three attempts.",
        callback=callback,
    )


@shared_task
def payouts_timeout_monitor():
    return payments_retry_timeouts()


@shared_task
def payouts_escalate_stuck_pending():
    return payments_escalate_pending_disbursements()


def _normalize_phone_suffix(phone: str) -> str:
    raw = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(raw) >= 9:
        return raw[-9:]
    return raw


@shared_task
def payments_advanced_reconciliation(
    chama_id: str | None = None,
    run_date: str | None = None,
):
    from apps.automations.services import AutomationJobRunner

    def callback():
        target_date = timezone.localdate()
        if run_date:
            target_date = date.fromisoformat(run_date)

        run = PaymentWorkflowService.run_reconciliation(chama_id=chama_id)

        intents = PaymentIntent.objects.all()
        if chama_id:
            intents = intents.filter(chama_id=chama_id)

        intents = intents.filter(created_at__date=target_date)
        successful_intents = intents.filter(status=PaymentIntentStatus.SUCCESS)

        c2b_success = MpesaC2BTransaction.objects.filter(
            trans_time__date=target_date,
            processing_status=MpesaC2BProcessingStatus.POSTED,
        )
        stk_success = MpesaSTKTransaction.objects.filter(
            created_at__date=target_date,
            status=PaymentIntentStatus.SUCCESS,
        )
        b2c_success = (
            PaymentIntent.objects.filter(
                b2c_payouts__created_at__date=target_date,
                b2c_payouts__status=MpesaB2CStatus.SUCCESS,
            )
            .distinct()
            .values_list("id", flat=True)
        )

        if chama_id:
            c2b_success = c2b_success.filter(chama_id=chama_id)
            stk_success = stk_success.filter(chama_id=chama_id)

        matched_exact = 0
        matched_fuzzy = 0
        missing_internally = []
        missing_provider_side = []
        duplicates = []

        for row in c2b_success.select_related("intent", "chama"):
            if row.intent_id:
                matched_exact += 1
                continue

            phone_suffix = _normalize_phone_suffix(row.phone)
            lower_time = row.trans_time - timedelta(hours=2)
            upper_time = row.trans_time + timedelta(hours=2)
            fuzzy = PaymentIntent.objects.filter(
                chama_id=row.chama_id,
                amount=row.amount,
                created_at__gte=lower_time,
                created_at__lte=upper_time,
            )
            if phone_suffix:
                fuzzy = fuzzy.filter(phone__endswith=phone_suffix)

            first_match = fuzzy.order_by("-created_at").first()
            if first_match:
                matched_fuzzy += 1
            else:
                missing_internally.append(
                    {
                        "provider_type": "c2b",
                        "trans_id": row.trans_id,
                        "amount": str(row.amount),
                        "phone_suffix": phone_suffix,
                    }
                )

        matched_exact += stk_success.filter(intent__isnull=False).count()
        missing_internally.extend(
            [
                {
                    "provider_type": "stk",
                    "checkout_request_id": tx.checkout_request_id,
                    "amount": str(tx.amount),
                }
                for tx in stk_success.filter(intent__isnull=True)[:100]
            ]
        )

        missing_provider_side_ids = successful_intents.exclude(
            Q(stk_transactions__status=PaymentIntentStatus.SUCCESS)
            | Q(c2b_transactions__processing_status=MpesaC2BProcessingStatus.POSTED)
            | Q(id__in=b2c_success)
        ).values("id", "intent_type", "amount")[:200]

        missing_provider_side.extend(
            [
                {
                    "intent_id": str(item["id"]),
                    "intent_type": item["intent_type"],
                    "amount": str(item["amount"]),
                }
                for item in missing_provider_side_ids
            ]
        )

        duplicate_stk_receipts = (
            stk_success.exclude(mpesa_receipt_number__isnull=True)
            .exclude(mpesa_receipt_number="")
            .values("mpesa_receipt_number")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        duplicates.extend(
            [
                {
                    "provider_type": "stk",
                    "receipt": row["mpesa_receipt_number"],
                    "count": row["total"],
                }
                for row in duplicate_stk_receipts
            ]
        )

        run_totals = dict(run.totals or {})
        run_totals.update(
            {
                "advanced_target_date": target_date.isoformat(),
                "matched_exact": matched_exact,
                "matched_fuzzy": matched_fuzzy,
                "missing_internally_count": len(missing_internally),
                "missing_provider_side_count": len(missing_provider_side),
                "duplicates_count": len(duplicates),
            }
        )
        run_anomalies = dict(run.anomalies or {})
        run_anomalies["advanced"] = {
            "missing_internally": missing_internally[:200],
            "missing_provider_side": missing_provider_side[:200],
            "duplicates": duplicates[:200],
        }

        run.totals = run_totals
        run.anomalies = run_anomalies
        if missing_internally or missing_provider_side or duplicates:
            run.status = "PARTIAL"
        run.save(update_fields=["totals", "anomalies", "status", "updated_at"])

        create_audit_log(
            actor=None,
            chama_id=chama_id,
            action="payments_advanced_reconciliation",
            entity_type="PaymentReconciliationRun",
            entity_id=run.id,
            metadata={
                "target_date": target_date.isoformat(),
                "matched_exact": matched_exact,
                "matched_fuzzy": matched_fuzzy,
                "missing_internally": len(missing_internally),
                "missing_provider_side": len(missing_provider_side),
                "duplicates": len(duplicates),
            },
        )

        return {
            "run_id": str(run.id),
            "target_date": target_date.isoformat(),
            "matched_exact": matched_exact,
            "matched_fuzzy": matched_fuzzy,
            "missing_internally": len(missing_internally),
            "missing_provider_side": len(missing_provider_side),
            "duplicates": len(duplicates),
        }

    return AutomationJobRunner.run_job(
        name="payments_advanced_reconciliation",
        schedule="30 22 * * *",
        description="Advanced provider-to-ledger reconciliation with exact and fuzzy matching.",
        callback=callback,
    )


@shared_task
def payments_fraud_pattern_detection_event(
    chama_id: str | None = None,
    event: str | None = None,
    intent_id: str | None = None,
):
    from apps.automations.services import AutomationJobRunner
    from apps.chama.models import Invite, InviteStatus
    from apps.notifications.models import NotificationType
    from apps.notifications.services import NotificationService

    def callback():
        now = timezone.now()
        window = now - timedelta(minutes=60)

        intents = PaymentIntent.objects.filter(created_at__gte=window)
        if chama_id:
            intents = intents.filter(chama_id=chama_id)

        rapid_withdrawals = (
            intents.filter(
                intent_type__in=[
                    PaymentIntentType.WITHDRAWAL,
                    PaymentIntentType.LOAN_DISBURSEMENT,
                ]
            )
            .values("chama_id")
            .annotate(total=Count("id"))
            .filter(total__gte=4)
        )
        repeated_failed_payments = (
            intents.filter(
                status__in=[
                    PaymentIntentStatus.FAILED,
                    PaymentIntentStatus.EXPIRED,
                    PaymentIntentStatus.CANCELLED,
                ]
            )
            .values("chama_id")
            .annotate(total=Count("id"))
            .filter(total__gte=5)
        )
        repeated_rejected_joins = (
            Invite.objects.filter(
                created_at__gte=window,
                status=InviteStatus.REJECTED,
            )
            .values("chama_id")
            .annotate(total=Count("id"))
            .filter(total__gte=5)
        )
        shared_device_patterns = (
            DeviceSession.objects.filter(created_at__gte=window, is_revoked=False)
            .values("ip_address", "user_agent")
            .annotate(users=Count("user_id", distinct=True))
            .filter(users__gte=3)
        )

        risk_flags = []
        if rapid_withdrawals.exists():
            risk_flags.append("rapid_repeated_withdrawals")
        if repeated_failed_payments.exists():
            risk_flags.append("multiple_failed_payments")
        if repeated_rejected_joins.exists():
            risk_flags.append("repeated_rejected_joins")
        if shared_device_patterns.exists():
            risk_flags.append("shared_device_across_accounts")

        notified = 0
        if risk_flags:
            chama_ids = set()
            for row in rapid_withdrawals:
                chama_ids.add(str(row["chama_id"]))
            for row in repeated_failed_payments:
                chama_ids.add(str(row["chama_id"]))
            for row in repeated_rejected_joins:
                if row["chama_id"]:
                    chama_ids.add(str(row["chama_id"]))

            if chama_id:
                chama_ids.add(str(chama_id))

            for target_chama in Chama.objects.filter(id__in=chama_ids, status=ChamaStatus.ACTIVE):
                admin_memberships = Membership.objects.select_related("user").filter(
                    chama=target_chama,
                    is_active=True,
                    is_approved=True,
                    status=MemberStatus.ACTIVE,
                    role__in=[MembershipRole.CHAMA_ADMIN, MembershipRole.TREASURER],
                )
                for admin in admin_memberships:
                    NotificationService.send_notification(
                        user=admin.user,
                        chama=target_chama,
                        channels=["in_app", "email"],
                        message=(
                            "Fraud monitoring alert: suspicious activity indicators were detected. "
                            "Review the security dashboard and reconciliation reports."
                        ),
                        subject="Suspicious activity alert",
                        notification_type=NotificationType.SECURITY_ALERT,
                        idempotency_key=(
                            f"payments-fraud-alert:{target_chama.id}:{admin.user_id}:{timezone.localdate().isoformat()}"
                        ),
                    )
                    notified += 1

        create_audit_log(
            actor=None,
            chama_id=chama_id,
            action="payments_fraud_pattern_detection",
            entity_type="PaymentIntent",
            entity_id=None,
            metadata={
                "event": event or "scheduled_scan",
                "intent_id": intent_id,
                "risk_flags": risk_flags,
                "rapid_withdrawal_chamas": [str(row["chama_id"]) for row in rapid_withdrawals],
                "failed_payment_chamas": [str(row["chama_id"]) for row in repeated_failed_payments],
                "shared_device_patterns": shared_device_patterns.count(),
                "alerts_sent": notified,
            },
        )
        return {
            "event": event or "scheduled_scan",
            "intent_id": intent_id,
            "risk_flags": risk_flags,
            "alerts_sent": notified,
        }

    return AutomationJobRunner.run_job(
        name="payments_fraud_pattern_detection",
        schedule="event",
        description="Rule-based fraud signal scan (withdrawals, failures, joins, shared devices).",
        callback=callback,
    )


@shared_task
def payments_fraud_pattern_detection():
    return payments_fraud_pattern_detection_event(event="scheduled")
