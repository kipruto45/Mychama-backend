"""Billing credit issuance and invoice allocation helpers."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import BillingCredit, BillingCreditAllocation, BillingEvent, Invoice, InvoiceLineItem


ZERO = Decimal('0.00')
STALE_RESERVATION_MINUTES = 30
DEFAULT_CREDIT_EXPIRY_DAYS = 90


def _normalized_amount(value) -> Decimal:
    return Decimal(value or ZERO).quantize(Decimal('0.01'))


def _credit_expiry_days() -> int:
    return max(
        0,
        int(getattr(settings, 'BILLING_CREDIT_EXPIRY_DAYS', DEFAULT_CREDIT_EXPIRY_DAYS)),
    )


def _reservation_timeout_minutes() -> int:
    return max(
        1,
        int(
            getattr(
                settings,
                'BILLING_CREDIT_RESERVATION_TIMEOUT_MINUTES',
                STALE_RESERVATION_MINUTES,
            )
        ),
    )


def _resolve_credit_expiry(expires_at=None):
    if expires_at is not None:
        return expires_at

    expiry_days = _credit_expiry_days()
    if expiry_days <= 0:
        return None
    return timezone.now() + timedelta(days=expiry_days)


def get_available_credit_balance(chama) -> Decimal:
    now = timezone.now()
    balance = (
        BillingCredit.objects.filter(chama=chama)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .aggregate(total=Sum('remaining_amount'))
        .get('total')
    )
    return _normalized_amount(balance)


def get_reserved_credit_balance(chama) -> Decimal:
    reserved = (
        BillingCreditAllocation.objects.filter(
            invoice__chama=chama,
            status=BillingCreditAllocation.RESERVED,
        )
        .aggregate(total=Sum('amount'))
        .get('total')
    )
    return _normalized_amount(reserved)


def get_reserved_credit_amount_for_credit(credit: BillingCredit) -> Decimal:
    reserved = (
        BillingCreditAllocation.objects.filter(
            credit=credit,
            status=BillingCreditAllocation.RESERVED,
        )
        .aggregate(total=Sum('amount'))
        .get('total')
    )
    return _normalized_amount(reserved)


def get_credit_summary(chama) -> dict:
    now = timezone.now()
    expiring_cutoff = now + timedelta(days=7)
    expiring_amount = (
        BillingCredit.objects.filter(
            chama=chama,
            remaining_amount__gt=ZERO,
            expires_at__gt=now,
            expires_at__lte=expiring_cutoff,
        )
        .aggregate(total=Sum('remaining_amount'))
        .get('total')
    )
    active_credits = BillingCredit.objects.filter(
        chama=chama,
        remaining_amount__gt=ZERO,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    ).count()
    return {
        'available_balance': str(get_available_credit_balance(chama)),
        'reserved_balance': str(get_reserved_credit_balance(chama)),
        'active_credits': active_credits,
        'expiring_within_7_days_amount': str(_normalized_amount(expiring_amount)),
        'default_expiry_days': _credit_expiry_days(),
        'currency': 'KES',
    }


def list_recent_credits(chama, *, limit: int = 10):
    return list(
        BillingCredit.objects.filter(chama=chama)
        .order_by('-created_at')[:limit]
    )


def get_credit_admin_summary(*, chama=None) -> dict:
    now = timezone.now()
    base_qs = BillingCredit.objects.all()
    reserved_qs = BillingCreditAllocation.objects.filter(
        status=BillingCreditAllocation.RESERVED,
    )
    if chama is not None:
        base_qs = base_qs.filter(chama=chama)
        reserved_qs = reserved_qs.filter(invoice__chama=chama)

    active_qs = base_qs.filter(remaining_amount__gt=ZERO).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )
    expired_qs = base_qs.filter(remaining_amount__gt=ZERO, expires_at__isnull=False, expires_at__lte=now)
    expiring_qs = base_qs.filter(
        remaining_amount__gt=ZERO,
        expires_at__isnull=False,
        expires_at__gt=now,
        expires_at__lte=now + timedelta(days=7),
    )

    return {
        'scope': 'chama' if chama is not None else 'global',
        'available_balance': str(_normalized_amount(active_qs.aggregate(total=Sum('remaining_amount')).get('total'))),
        'reserved_balance': str(
            _normalized_amount(reserved_qs.aggregate(total=Sum('amount')).get('total'))
        ),
        'expired_balance': str(_normalized_amount(expired_qs.aggregate(total=Sum('remaining_amount')).get('total'))),
        'expiring_within_7_days': str(
            _normalized_amount(expiring_qs.aggregate(total=Sum('remaining_amount')).get('total'))
        ),
        'active_credits': active_qs.count(),
        'expiring_credits': expiring_qs.count(),
        'default_expiry_days': _credit_expiry_days(),
        'reservation_timeout_minutes': _reservation_timeout_minutes(),
        'currency': 'KES',
    }


def issue_billing_credit(
    *,
    chama,
    amount,
    source_type: str = BillingCredit.REFERRAL,
    source_reference: str = '',
    description: str = '',
    expires_at=None,
    metadata: dict | None = None,
    performed_by=None,
) -> BillingCredit | None:
    normalized_amount = _normalized_amount(amount)
    if normalized_amount <= ZERO:
        return None

    credit = BillingCredit.objects.create(
        chama=chama,
        source_type=source_type,
        source_reference=source_reference,
        description=description,
        total_amount=normalized_amount,
        remaining_amount=normalized_amount,
        expires_at=_resolve_credit_expiry(expires_at),
        metadata=metadata or {},
    )
    BillingEvent.objects.create(
        chama=chama,
        event_type=BillingEvent.PLAN_CHANGED,
        details={
            'action': 'billing_credit_issued',
            'credit_id': str(credit.id),
            'amount': str(normalized_amount),
            'source_type': source_type,
            'source_reference': source_reference,
        },
        performed_by=performed_by,
    )
    return credit


@transaction.atomic
def update_billing_credit(
    credit: BillingCredit,
    *,
    update_remaining: bool = False,
    remaining_amount=None,
    update_description: bool = False,
    description: str = '',
    update_expires_at: bool = False,
    expires_at=None,
    performed_by=None,
) -> BillingCredit:
    changes: dict[str, str] = {}
    update_fields = ['updated_at']

    if update_remaining:
        new_remaining = _normalized_amount(remaining_amount)
        current_remaining = _normalized_amount(credit.remaining_amount)
        if new_remaining > current_remaining:
            credit.total_amount = _normalized_amount(
                _normalized_amount(credit.total_amount) + (new_remaining - current_remaining)
            )
            if 'total_amount' not in update_fields:
                update_fields.append('total_amount')
        credit.remaining_amount = new_remaining
        update_fields.append('remaining_amount')
        changes['remaining_amount'] = str(new_remaining)

    if update_description:
        credit.description = description
        update_fields.append('description')
        changes['description'] = description

    if update_expires_at:
        credit.expires_at = expires_at
        update_fields.append('expires_at')
        changes['expires_at'] = expires_at.isoformat() if expires_at else ''

    if len(update_fields) > 1:
        credit.save(update_fields=update_fields)
        BillingEvent.objects.create(
            chama=credit.chama,
            event_type=BillingEvent.PLAN_CHANGED,
            details={
                'action': 'billing_credit_updated',
                'credit_id': str(credit.id),
                'changes': changes,
            },
            performed_by=performed_by,
        )

    return credit


@transaction.atomic
def revoke_billing_credit(credit: BillingCredit, *, performed_by=None) -> BillingCredit:
    reserved_amount = get_reserved_credit_amount_for_credit(credit)
    if reserved_amount > ZERO:
        raise ValueError(
            'This credit is currently reserved by a pending invoice and cannot be revoked yet.'
        )

    if _normalized_amount(credit.remaining_amount) <= ZERO:
        return credit

    metadata = dict(credit.metadata or {})
    metadata['revoked'] = True
    metadata['revoked_at'] = timezone.now().isoformat()
    if performed_by is not None:
        metadata['revoked_by'] = str(performed_by.id)

    credit.remaining_amount = ZERO
    if not credit.expires_at or credit.expires_at > timezone.now():
        credit.expires_at = timezone.now()
    credit.metadata = metadata
    credit.save(update_fields=['remaining_amount', 'expires_at', 'metadata', 'updated_at'])

    BillingEvent.objects.create(
        chama=credit.chama,
        event_type=BillingEvent.PLAN_CHANGED,
        details={
            'action': 'billing_credit_revoked',
            'credit_id': str(credit.id),
        },
        performed_by=performed_by,
    )
    return credit


def refresh_invoice_totals(invoice: Invoice) -> Invoice:
    subtotal = (
        invoice.line_items.aggregate(total=Sum('total_price')).get('total') or ZERO
    )
    subtotal = _normalized_amount(max(ZERO, subtotal))

    tax_rate = Decimal('0.00')
    if hasattr(invoice, 'tax_amount'):
        from django.conf import settings

        tax_rate = Decimal(str(getattr(settings, 'BILLING_TAX_RATE', '0.00')))
    tax_amount = (subtotal * tax_rate).quantize(Decimal('0.01'))
    total_amount = (subtotal + tax_amount).quantize(Decimal('0.01'))

    invoice.subtotal = subtotal
    invoice.tax_amount = tax_amount
    invoice.total_amount = total_amount
    invoice.save(update_fields=['subtotal', 'tax_amount', 'total_amount', 'updated_at'])
    return invoice


def release_stale_credit_reservations(*, chama, older_than_minutes: int | None = None) -> int:
    timeout_minutes = older_than_minutes or _reservation_timeout_minutes()
    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    stale_invoice_ids = list(
        Invoice.objects.filter(
            chama=chama,
            status=Invoice.PENDING,
            created_at__lt=cutoff,
            credit_allocations__status=BillingCreditAllocation.RESERVED,
        )
        .values_list('id', flat=True)
        .distinct()
    )
    released = 0
    for invoice_id in stale_invoice_ids:
        invoice = Invoice.objects.filter(id=invoice_id).first()
        if invoice:
            released_count = release_reserved_credits(invoice)
            if released_count:
                invoice.status = Invoice.VOID
                invoice.save(update_fields=['status', 'updated_at'])
                released += released_count
    return released


def release_stale_credit_reservations_for_all(*, older_than_minutes: int | None = None) -> dict:
    timeout_minutes = older_than_minutes or _reservation_timeout_minutes()
    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    stale_invoice_ids = list(
        Invoice.objects.filter(
            status=Invoice.PENDING,
            created_at__lt=cutoff,
            credit_allocations__status=BillingCreditAllocation.RESERVED,
        )
        .values_list('id', flat=True)
        .distinct()
    )

    released_allocations = 0
    released_invoices = 0
    for invoice_id in stale_invoice_ids:
        invoice = Invoice.objects.filter(id=invoice_id).first()
        if not invoice:
            continue
        released = release_reserved_credits(invoice)
        if released:
            invoice.status = Invoice.VOID
            invoice.save(update_fields=['status', 'updated_at'])
            released_invoices += 1
            released_allocations += released

    return {
        'released_invoices': released_invoices,
        'released_allocations': released_allocations,
        'reservation_timeout_minutes': timeout_minutes,
    }


@transaction.atomic
def reserve_credits_for_invoice(invoice: Invoice, requested_amount) -> Decimal:
    release_stale_credit_reservations(chama=invoice.chama)

    amount_to_apply = _normalized_amount(requested_amount)
    if amount_to_apply <= ZERO:
        return ZERO

    now = timezone.now()
    applied_total = ZERO
    credits = (
        BillingCredit.objects.select_for_update()
        .filter(chama=invoice.chama, remaining_amount__gt=ZERO)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .order_by('created_at', 'id')
    )

    for credit in credits:
        if applied_total >= amount_to_apply:
            break
        available = _normalized_amount(credit.remaining_amount)
        if available <= ZERO:
            continue

        allocation_amount = min(amount_to_apply - applied_total, available)
        allocation_amount = _normalized_amount(allocation_amount)
        if allocation_amount <= ZERO:
            continue

        credit.remaining_amount = _normalized_amount(credit.remaining_amount - allocation_amount)
        credit.save(update_fields=['remaining_amount', 'updated_at'])
        BillingCreditAllocation.objects.create(
            credit=credit,
            invoice=invoice,
            amount=allocation_amount,
            status=BillingCreditAllocation.RESERVED,
        )
        applied_total += allocation_amount

    if applied_total > ZERO:
        InvoiceLineItem.objects.create(
            invoice=invoice,
            title='Referral credit',
            description='Automatic credit applied from your referral rewards balance',
            quantity=1,
            unit_price=-applied_total,
            total_price=-applied_total,
            metadata={'type': 'referral_credit'},
        )
        refresh_invoice_totals(invoice)

    return applied_total


@transaction.atomic
def apply_reserved_credits(invoice: Invoice) -> Decimal:
    allocations = list(
        BillingCreditAllocation.objects.select_for_update()
        .filter(invoice=invoice, status=BillingCreditAllocation.RESERVED)
        .select_related('credit')
    )
    if not allocations:
        return ZERO

    applied_total = ZERO
    now = timezone.now()
    for allocation in allocations:
        allocation.status = BillingCreditAllocation.APPLIED
        allocation.applied_at = now
        allocation.save(update_fields=['status', 'applied_at', 'updated_at'])
        applied_total += _normalized_amount(allocation.amount)

    BillingEvent.objects.create(
        chama=invoice.chama,
        event_type=BillingEvent.PAYMENT_SUCCEEDED,
        details={
            'action': 'billing_credit_applied',
            'invoice_id': str(invoice.id),
            'invoice_number': invoice.invoice_number,
            'amount': str(applied_total),
        },
    )
    return applied_total


@transaction.atomic
def release_reserved_credits(invoice: Invoice) -> int:
    allocations = list(
        BillingCreditAllocation.objects.select_for_update()
        .filter(invoice=invoice, status=BillingCreditAllocation.RESERVED)
        .select_related('credit')
    )
    if not allocations:
        return 0

    now = timezone.now()
    released = 0
    for allocation in allocations:
        credit = allocation.credit
        credit.remaining_amount = _normalized_amount(credit.remaining_amount + allocation.amount)
        credit.save(update_fields=['remaining_amount', 'updated_at'])
        allocation.status = BillingCreditAllocation.RELEASED
        allocation.released_at = now
        allocation.save(update_fields=['status', 'released_at', 'updated_at'])
        released += 1

    BillingEvent.objects.create(
        chama=invoice.chama,
        event_type=BillingEvent.PAYMENT_FAILED,
        details={
            'action': 'billing_credit_released',
            'invoice_id': str(invoice.id),
            'invoice_number': invoice.invoice_number,
            'released_allocations': released,
        },
    )
    return released
