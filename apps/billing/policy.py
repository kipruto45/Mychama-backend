"""
Billing policy, proration, and plan transition rules.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from .entitlements import PLAN_ENTITLEMENTS
from .metering import sync_usage_limits
from .models import BillingEvent, BillingRule, FeatureOverride, Plan, Subscription


def _default_retry_schedule() -> list[int]:
    configured = getattr(settings, 'BILLING_PAYMENT_RETRY_SCHEDULE', [1, 3, 7])
    if isinstance(configured, str):
        return [int(item.strip()) for item in configured.split(',') if item.strip()]
    return [int(item) for item in configured]


def get_billing_rule(chama=None) -> BillingRule:
    if chama is not None:
        rule = BillingRule.objects.filter(chama=chama, is_active=True).first()
        if rule:
            return rule

    rule = BillingRule.objects.filter(chama__isnull=True, is_active=True).first()
    if rule:
        return rule

    return BillingRule(
        name='default',
        grace_period_days=int(getattr(settings, 'BILLING_DEFAULT_GRACE_DAYS', 7)),
        enforcement_mode=str(
            getattr(settings, 'BILLING_ENFORCEMENT_MODE', BillingRule.HARD_LOCK)
        ),
        upgrade_approval_threshold=Decimal(
            str(getattr(settings, 'BILLING_UPGRADE_APPROVAL_THRESHOLD', '0.00'))
        ),
        auto_renew_enabled=bool(getattr(settings, 'BILLING_AUTO_RENEW_ENABLED', True)),
        payment_retry_schedule=_default_retry_schedule(),
        allow_enterprise_overrides=bool(
            getattr(settings, 'BILLING_ALLOW_ENTERPRISE_OVERRIDES', True)
        ),
        is_active=True,
    )


def calculate_plan_amount(plan: Plan, billing_cycle: str) -> Decimal:
    return plan.yearly_price if billing_cycle == Subscription.YEARLY else plan.monthly_price


def calculate_prorated_charge(chama, new_plan: Plan, billing_cycle: str) -> dict:
    current_subscription = (
        Subscription.objects.filter(
            chama=chama,
            status__in=[Subscription.TRIALING, Subscription.ACTIVE],
        )
        .select_related('plan')
        .order_by('-current_period_end', '-created_at')
        .first()
    )

    full_amount = calculate_plan_amount(new_plan, billing_cycle)
    if not current_subscription or current_subscription.plan.code == Plan.FREE:
        return {
            'charge_amount': full_amount,
            'credit_amount': Decimal('0.00'),
            'full_amount': full_amount,
            'prorated': False,
            'requires_approval': full_amount
            >= get_billing_rule(chama).upgrade_approval_threshold,
        }

    current_amount = calculate_plan_amount(
        current_subscription.plan,
        current_subscription.billing_cycle or billing_cycle,
    )
    if full_amount <= current_amount:
        return {
            'charge_amount': Decimal('0.00'),
            'credit_amount': Decimal('0.00'),
            'full_amount': full_amount,
            'prorated': False,
            'requires_approval': False,
        }

    if not current_subscription.current_period_end or not current_subscription.current_period_start:
        credit = Decimal('0.00')
    else:
        total_seconds = max(
            1,
            (current_subscription.current_period_end - current_subscription.current_period_start).total_seconds(),
        )
        remaining_seconds = max(
            0,
            (current_subscription.current_period_end - timezone.now()).total_seconds(),
        )
        credit = (current_amount * Decimal(remaining_seconds / total_seconds)).quantize(
            Decimal('0.01')
        )

    charge_amount = max(Decimal('0.00'), (full_amount - credit)).quantize(Decimal('0.01'))
    rule = get_billing_rule(chama)
    return {
        'charge_amount': charge_amount,
        'credit_amount': credit,
        'full_amount': full_amount,
        'prorated': charge_amount != full_amount,
        'requires_approval': charge_amount >= rule.upgrade_approval_threshold,
    }


def validate_plan_change(chama, new_plan: Plan) -> dict:
    usage = sync_usage_limits(chama)
    target_features = dict(PLAN_ENTITLEMENTS.get(new_plan.code, {}))
    target_features.update(new_plan.features or {})

    errors = []
    if usage.get('members', {}).get('used', 0) > int(target_features.get('seat_limit', 0) or 0):
        errors.append(
            f"Cannot downgrade: active members exceed the {target_features.get('seat_limit', 0)} member limit."
        )
    if usage.get('sms', {}).get('used', 0) > int(target_features.get('sms_limit', 0) or 0):
        errors.append(
            f"Cannot downgrade: SMS usage exceeds the {target_features.get('sms_limit', 0)} message limit."
        )
    if usage.get('storage_mb', {}).get('used', 0) > int(
        target_features.get('storage_limit_mb', 0) or 0
    ):
        errors.append(
            'Cannot downgrade: storage usage exceeds the target plan storage allowance.'
        )

    disallowed_overrides = list(
        FeatureOverride.objects.filter(chama=chama).exclude(
            feature_key__in=[
                key for key, enabled in target_features.items() if isinstance(enabled, bool) and enabled
            ]
        )
    )
    if disallowed_overrides:
        errors.append(
            'Cannot downgrade while custom enterprise feature overrides are still active.'
        )

    if errors:
        raise ValueError(' '.join(errors))

    return {
        'usage': usage,
        'target_plan': new_plan.code,
    }


def schedule_plan_change(chama, new_plan: Plan, *, performed_by=None, billing_cycle: str = Subscription.MONTHLY) -> dict:
    current_subscription = (
        Subscription.objects.filter(
            chama=chama,
            status__in=[Subscription.TRIALING, Subscription.ACTIVE],
        )
        .select_related('plan')
        .order_by('-current_period_end', '-created_at')
        .first()
    )

    target_amount = calculate_plan_amount(new_plan, billing_cycle)
    current_amount = (
        calculate_plan_amount(
            current_subscription.plan,
            current_subscription.billing_cycle or billing_cycle,
        )
        if current_subscription
        else Decimal('0.00')
    )

    if current_subscription and target_amount < current_amount:
        validate_plan_change(chama, new_plan)
        current_subscription.scheduled_plan = new_plan
        current_subscription.scheduled_change_at = (
            current_subscription.current_period_end or timezone.now()
        )
        current_subscription.save(
            update_fields=['scheduled_plan', 'scheduled_change_at', 'updated_at']
        )
        BillingEvent.objects.create(
            chama=chama,
            event_type=BillingEvent.PLAN_CHANGED,
            details={
                'old_plan': current_subscription.plan.code,
                'new_plan': new_plan.code,
                'mode': 'scheduled_downgrade',
                'effective_at': current_subscription.scheduled_change_at.isoformat()
                if current_subscription.scheduled_change_at
                else None,
            },
            performed_by=performed_by,
        )
        return {
            'scheduled': True,
            'effective_at': current_subscription.scheduled_change_at,
        }

    return {
        'scheduled': False,
        'effective_at': timezone.now(),
    }


def apply_due_scheduled_changes(now=None) -> int:
    now = now or timezone.now()
    updated = 0

    due_subscriptions = Subscription.objects.filter(
        scheduled_plan__isnull=False,
        scheduled_change_at__isnull=False,
        scheduled_change_at__lte=now,
    ).select_related('scheduled_plan', 'plan', 'chama')

    if not due_subscriptions.exists():
        return 0

    from .services import change_plan

    for subscription in due_subscriptions:
        target_plan = subscription.scheduled_plan
        change_plan(
            subscription.chama,
            target_plan,
            performed_by=None,
            billing_cycle=subscription.billing_cycle or Subscription.MONTHLY,
            provider=subscription.provider,
        )
        subscription.scheduled_plan = None
        subscription.scheduled_change_at = None
        subscription.save(
            update_fields=['scheduled_plan', 'scheduled_change_at', 'updated_at']
        )
        updated += 1

    return updated
