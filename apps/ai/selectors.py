from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.ai.models import KnowledgeChunk, KnowledgeDocument
from apps.chama.models import MembershipRole
from apps.chama.permissions import get_membership
from apps.chama.services import get_effective_role
from apps.finance.models import Contribution, InstallmentSchedule, Loan
from apps.finance.services import FinanceService
from apps.issues.models import Issue, IssueStatus
from apps.meetings.models import Meeting
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from apps.payments.models import MpesaB2CPayout, MpesaB2CStatus, PaymentIntent

ADMIN_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.TREASURER,
    MembershipRole.SECRETARY,
}


def mask_phone(phone: str) -> str:
    raw = str(phone or "")
    if len(raw) < 7:
        return "***"
    return f"{raw[:5]}****{raw[-3:]}"


def require_membership(user, chama_id):
    membership = get_membership(user, chama_id)
    if not membership:
        raise PermissionDenied("You are not an approved active member of this chama.")
    return membership


def _effective_role(membership):
    return get_effective_role(membership.user, membership.chama_id, membership)


def require_admin_membership(user, chama_id):
    membership = require_membership(user, chama_id)
    if _effective_role(membership) not in ADMIN_ROLES:
        raise PermissionDenied("Only chama admin/treasurer/secretary can do this.")
    return membership


def require_member_scope(user, chama_id, member_id):
    membership = require_membership(user, chama_id)
    if str(user.id) != str(member_id) and _effective_role(membership) not in ADMIN_ROLES:
        raise PermissionDenied("You cannot access another member's financial data.")
    return membership


def get_member_contribution_summary(*, chama_id, member_id, from_date=None, to_date=None):
    queryset = Contribution.objects.filter(chama_id=chama_id, member_id=member_id)
    if from_date:
        queryset = queryset.filter(date_paid__gte=from_date)
    if to_date:
        queryset = queryset.filter(date_paid__lte=to_date)

    total = queryset.aggregate(
        total=Coalesce(
            Sum("amount"),
            Value(Decimal("0.00"), output_field=DecimalField()),
        )
    )["total"]
    items = list(
        queryset.order_by("-date_paid", "-created_at")[:30].values(
            "id",
            "date_paid",
            "amount",
            "receipt_code",
            "contribution_type__name",
        )
    )
    return {
        "member_id": str(member_id),
        "total_contributed": str(total),
        "count": queryset.count(),
        "records": [
            {
                "id": str(item["id"]),
                "date_paid": item["date_paid"].isoformat(),
                "amount": str(item["amount"]),
                "receipt_code": item["receipt_code"],
                "contribution_type": item["contribution_type__name"],
            }
            for item in items
        ],
    }


def get_member_loan_summary(*, chama_id, member_id):
    loans = Loan.objects.filter(chama_id=chama_id, member_id=member_id).order_by(
        "-requested_at"
    )
    payload = []
    for loan in loans[:20]:
        repayments_total = loan.repayments.aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00"), output_field=DecimalField()),
            )
        )["total"]
        payload.append(
            {
                "id": str(loan.id),
                "status": loan.status,
                "principal": str(loan.principal),
                "interest_rate": str(loan.interest_rate),
                "duration_months": loan.duration_months,
                "requested_at": loan.requested_at.isoformat(),
                "outstanding": str(max(loan.principal - repayments_total, Decimal("0.00"))),
            }
        )
    return {
        "member_id": str(member_id),
        "count": loans.count(),
        "loans": payload,
    }


def get_member_next_installment(*, chama_id, member_id, loan_id=None):
    queryset = InstallmentSchedule.objects.select_related("loan").filter(
        loan__chama_id=chama_id,
        loan__member_id=member_id,
        status__in=["due", "overdue"],
    )
    if loan_id:
        queryset = queryset.filter(loan_id=loan_id)

    installment = queryset.order_by("due_date", "created_at").first()
    if not installment:
        return {"next_installment": None}

    return {
        "next_installment": {
            "installment_id": str(installment.id),
            "loan_id": str(installment.loan_id),
            "due_date": installment.due_date.isoformat(),
            "expected_amount": str(installment.expected_amount),
            "status": installment.status,
        }
    }


def get_chama_portfolio_summary(*, chama_id):
    return FinanceService.compute_loan_portfolio(chama_id, mask_members=False)


def list_overdue_installments(*, chama_id):
    rows = InstallmentSchedule.objects.select_related("loan", "loan__member").filter(
        loan__chama_id=chama_id,
        status="overdue",
    )
    return {
        "count": rows.count(),
        "items": [
            {
                "installment_id": str(item.id),
                "loan_id": str(item.loan_id),
                "member_id": str(item.loan.member_id),
                "member_name": item.loan.member.full_name,
                "expected_amount": str(item.expected_amount),
                "due_date": item.due_date.isoformat(),
            }
            for item in rows.order_by("due_date")[:100]
        ],
    }


def get_payment_status(*, intent_id):
    intent = get_object_or_404(PaymentIntent, id=intent_id)
    return {
        "intent_id": str(intent.id),
        "status": intent.status,
        "intent_type": intent.intent_type,
        "amount": str(intent.amount),
        "created_at": intent.created_at.isoformat(),
        "reference_type": intent.reference_type,
        "reference_id": str(intent.reference_id) if intent.reference_id else None,
    }


def list_my_payments(*, chama_id, member_id):
    intents = PaymentIntent.objects.filter(
        chama_id=chama_id,
        created_by_id=member_id,
    ).order_by("-created_at")
    return {
        "count": intents.count(),
        "items": [
            {
                "intent_id": str(item.id),
                "intent_type": item.intent_type,
                "status": item.status,
                "amount": str(item.amount),
                "phone": mask_phone(item.phone),
                "created_at": item.created_at.isoformat(),
            }
            for item in intents[:100]
        ],
    }


def list_failed_or_pending_payouts(*, chama_id):
    payouts = MpesaB2CPayout.objects.filter(
        chama_id=chama_id,
        status__in=[MpesaB2CStatus.PENDING, MpesaB2CStatus.FAILED, MpesaB2CStatus.TIMEOUT],
    ).order_by("-created_at")
    return {
        "count": payouts.count(),
        "items": [
            {
                "payout_id": str(item.id),
                "intent_id": str(item.intent_id),
                "status": item.status,
                "amount": str(item.amount),
                "phone": mask_phone(item.phone),
                "created_at": item.created_at.isoformat(),
                "transaction_id": item.transaction_id,
            }
            for item in payouts[:100]
        ],
    }


def get_issue(*, issue_id):
    issue = get_object_or_404(Issue, id=issue_id)
    return {
        "id": str(issue.id),
        "chama_id": str(issue.chama_id),
        "title": issue.title,
        "description": issue.description,
        "category": issue.category,
        "priority": issue.priority,
        "status": issue.status,
        "reported_user_id": str(issue.reported_user_id) if issue.reported_user_id else None,
    }


def list_open_issues(*, chama_id):
    queryset = Issue.objects.filter(
        chama_id=chama_id,
        status__in=[IssueStatus.OPEN, IssueStatus.IN_REVIEW, IssueStatus.ASSIGNED],
    )
    return {
        "count": queryset.count(),
        "items": [
            {
                "id": str(issue.id),
                "title": issue.title,
                "category": issue.category,
                "priority": issue.priority,
                "status": issue.status,
                "created_at": issue.created_at.isoformat(),
            }
            for issue in queryset.order_by("-created_at")[:100]
        ],
    }


def get_meeting_minutes(*, meeting_id):
    meeting = get_object_or_404(Meeting, id=meeting_id)
    return {
        "meeting_id": str(meeting.id),
        "chama_id": str(meeting.chama_id),
        "title": meeting.title,
        "date": meeting.date.isoformat(),
        "agenda": meeting.agenda,
        "minutes_text": meeting.minutes_text,
    }


def list_upcoming_meetings(*, chama_id):
    now = timezone.now()
    upcoming = Meeting.objects.filter(
        chama_id=chama_id,
        date__gte=now,
    ).order_by("date")
    return {
        "count": upcoming.count(),
        "items": [
            {
                "meeting_id": str(item.id),
                "title": item.title,
                "date": item.date.isoformat(),
            }
            for item in upcoming[:20]
        ],
    }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    norm_a = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    norm_b = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search_kb(*, chama_id, query_embedding: list[float], top_k: int = 5):
    docs = KnowledgeDocument.objects.filter(chama_id=chama_id)
    chunks = KnowledgeChunk.objects.filter(document__in=docs).select_related("document")
    scored = []
    for chunk in chunks:
        if not chunk.embedding_vector:
            continue
        score = cosine_similarity(query_embedding, list(chunk.embedding_vector))
        scored.append((score, chunk))

    scored.sort(key=lambda row: row[0], reverse=True)
    return [
        {
            "chunk_id": str(item.id),
            "document_id": str(item.document_id),
            "document_title": item.document.title,
            "score": round(score, 4),
            "text": item.chunk_text,
        }
        for score, item in scored[:top_k]
    ]


def draft_notification(*, message: str, channels: list[str]):
    return {
        "draft": True,
        "message": message,
        "channels": channels,
    }


def send_notification_with_confirm(*, user, chama, message: str, channels: list[str], confirm: bool):
    if not confirm:
        return {
            "sent": False,
            "detail": "Confirmation required before sending notification.",
        }

    notification = NotificationService.send_notification(
        user=user,
        chama=chama,
        message=message,
        channels=channels,
        notification_type=NotificationType.SYSTEM,
        idempotency_key=(
            f"ai-send-notification:{chama.id}:{user.id}:{timezone.localdate().isoformat()}:{hash(message)}"
        ),
    )
    return {
        "sent": True,
        "notification_id": str(notification.id),
        "status": notification.status,
    }


def find_suspicious_transactions(*, chama_id):
    duplicated_receipts = (
        PaymentIntent.objects.filter(chama_id=chama_id)
        .values("idempotency_key")
        .annotate(total=Count("id"))
    )
    duplicates = [
        row["idempotency_key"]
        for row in duplicated_receipts
        if row["total"] and row["total"] > 1
    ]

    odd_withdrawals = PaymentIntent.objects.filter(
        chama_id=chama_id,
        intent_type="WITHDRAWAL",
        amount__gte=Decimal("100000.00"),
    ).order_by("-created_at")

    return {
        "duplicate_idempotency_keys": duplicates,
        "odd_withdrawals": [
            {
                "intent_id": str(item.id),
                "amount": str(item.amount),
                "created_at": item.created_at.isoformat(),
            }
            for item in odd_withdrawals[:20]
        ],
    }


def recent_chama_summary(*, chama_id):
    from_date = timezone.localdate() - timedelta(days=30)
    return {
        "dashboard": FinanceService.compute_chama_dashboard(chama_id),
        "member_statement_window_start": from_date.isoformat(),
    }
