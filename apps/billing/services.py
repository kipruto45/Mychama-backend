"""
Billing Services
Business logic for subscription management and feature gating
"""
from datetime import timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from django.db.models import Q, Sum, Count

from .invoicing import (
    create_invoice,
    generate_invoice_pdf,
    mark_invoice_failed,
    mark_invoice_paid,
    send_invoice_email,
)
from .credits import (
    get_available_credit_balance,
    get_credit_admin_summary,
    get_credit_summary,
    release_stale_credit_reservations_for_all,
    reserve_credits_for_invoice,
)
from .metering import reset_due_usage_metrics, sync_usage_limits, usage_within_limit
from .metering import increment_usage
from .models import (
    BillingCredit,
    BillingEvent,
    BillingRule,
    FeatureOverride,
    Invoice,
    Plan,
    SeatUsage,
    Subscription,
    UsageMetric,
)
from .policy import (
    apply_due_scheduled_changes,
    calculate_plan_amount,
    calculate_prorated_charge,
    get_billing_rule,
)
from .security import decrypt_billing_metadata, encrypt_billing_metadata
from .entitlements import PLAN_ENTITLEMENTS


# Cache timeout for entitlements (60 seconds)
ENTITLEMENTS_CACHE_TIMEOUT = 60
TRIAL_DAYS = 30
BILLING_CYCLE_DAYS = {
    'monthly': 30,
    'yearly': 365,
}
DEFAULT_PLAN_DEFINITIONS = (
    {
        'code': Plan.FREE,
        'name': 'Free Trial',
        'description': 'Included automatically for every new chama during the initial 30-day trial window.',
        'monthly_price': 0,
        'yearly_price': 0,
        'features': PLAN_ENTITLEMENTS[Plan.FREE],
        'is_active': True,
        'is_featured': False,
        'sort_order': 1,
    },
    {
        'code': Plan.PRO,
        'name': 'Pro',
        'description': 'For growing chamas that need advanced features, exports, and automation.',
        'monthly_price': 4999,
        'yearly_price': 49990,
        'features': PLAN_ENTITLEMENTS[Plan.PRO],
        'is_active': True,
        'is_featured': True,
        'sort_order': 2,
        'stripe_monthly_price_id': 'price_pro_monthly',
        'stripe_yearly_price_id': 'price_pro_yearly',
    },
    {
        'code': Plan.ENTERPRISE,
        'name': 'Enterprise',
        'description': 'For large chamas requiring unlimited members, priority support, and custom integrations.',
        'monthly_price': 19999,
        'yearly_price': 199990,
        'features': PLAN_ENTITLEMENTS[Plan.ENTERPRISE],
        'is_active': True,
        'is_featured': False,
        'sort_order': 3,
        'stripe_monthly_price_id': 'price_enterprise_monthly',
        'stripe_yearly_price_id': 'price_enterprise_yearly',
    },
)


def ensure_default_plans():
    """Create missing plans and backfill any newly introduced default features."""
    for definition in DEFAULT_PLAN_DEFINITIONS:
        defaults = dict(definition)
        default_features = defaults.pop('features', {})
        plan, created = Plan.objects.get_or_create(
            code=definition['code'],
            defaults={
                **defaults,
                'features': default_features,
            },
        )
        if created:
            continue

        merged_features = dict(default_features)
        merged_features.update(plan.features or {})

        fields_to_update = []
        if plan.features != merged_features:
            plan.features = merged_features
            fields_to_update.append('features')

        if not plan.is_active and defaults.get('is_active', True):
            plan.is_active = True
            fields_to_update.append('is_active')

        if fields_to_update:
            plan.save(update_fields=fields_to_update)


def get_free_plan() -> Optional[Plan]:
    """Get or create the free plan."""
    ensure_default_plans()
    return Plan.objects.filter(code=Plan.FREE, is_active=True).first()


def get_latest_subscription(chama) -> Optional[Subscription]:
    """Get the most recent subscription record for a chama."""
    return Subscription.objects.filter(chama=chama).select_related('plan').order_by(
        '-current_period_end',
        '-created_at',
    ).first()


def ensure_trial_subscription(chama) -> Optional[Subscription]:
    """Provision a single 30-day free trial for chamas that do not have billing yet."""
    existing_subscription = get_latest_subscription(chama)
    if existing_subscription:
        return existing_subscription

    free_plan = get_free_plan()
    if not free_plan:
        return None

    trial_start = chama.created_at or timezone.now()
    trial_end = trial_start + timedelta(days=TRIAL_DAYS)

    subscription = Subscription.objects.create(
        chama=chama,
        plan=free_plan,
        status=Subscription.TRIALING,
        provider=Subscription.MANUAL,
        billing_cycle=Subscription.MONTHLY,
        auto_renew=False,
        current_period_start=trial_start,
        current_period_end=trial_end,
    )

    BillingEvent.objects.create(
        chama=chama,
        event_type=BillingEvent.SUBSCRIPTION_CREATED,
        details={
            'plan': free_plan.code,
            'provider': Subscription.MANUAL,
            'subscription_id': str(subscription.id),
            'billing_cycle': 'trial',
            'trial_days': TRIAL_DAYS,
        }
    )

    clear_entitlements_cache(chama)
    return subscription


def get_active_subscription(chama) -> Optional[Subscription]:
    """Get active subscription for a chama"""
    try:
        ensure_trial_subscription(chama)
        return Subscription.objects.filter(
            chama=chama,
            status__in=[Subscription.TRIALING, Subscription.ACTIVE],
        ).filter(
            Q(current_period_end__isnull=True) | Q(current_period_end__gt=timezone.now())
        ).select_related('plan').order_by(
            '-current_period_end',
            '-created_at',
        ).first()
    except Exception:
        return None


def get_active_chama_from_request(request) -> Optional[Any]:
    """
    Get active chama from request
    Prefers X-CHAMA-ID header, else falls back to user's active chama
    """
    from apps.accounts.models import UserPreference
    from apps.chama.models import Chama, ChamaStatus, Membership, MemberStatus
    
    request_data = getattr(request, 'data', {}) or {}
    request_query = getattr(request, 'query_params', {}) or {}

    candidate_chama_ids = [
        request.headers.get('X-CHAMA-ID'),
        request_query.get('chama_id'),
        request_data.get('chama_id') if hasattr(request_data, 'get') else None,
    ]
    for chama_id in candidate_chama_ids:
        if not chama_id:
            continue
        try:
            return Chama.objects.get(id=chama_id, status=ChamaStatus.ACTIVE)
        except Chama.DoesNotExist:
            continue
    
    # Fall back to user's active chama
    if hasattr(request, 'user') and request.user.is_authenticated:
        try:
            pref = UserPreference.objects.get(user=request.user)
            if pref.active_chama_id:
                return Chama.objects.get(id=pref.active_chama_id, status=ChamaStatus.ACTIVE)
        except (UserPreference.DoesNotExist, Chama.DoesNotExist):
            pass

        membership = Membership.objects.filter(
            user=request.user,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            chama__status=ChamaStatus.ACTIVE,
        ).select_related('chama').order_by('-created_at').first()
        if membership:
            return membership.chama
    
    return None


def get_entitlements(chama, use_cache: bool = True) -> Dict[str, Any]:
    """
    Get effective entitlements for a chama
    Reads active subscription plan.features and applies FeatureOverride
    Caches results for 60 seconds
    """
    cache_key = f'entitlements_{chama.id}'
    
    if use_cache:
        cached = cache.get(cache_key)
        if cached:
            return cached
    
    # Start with FREE plan defaults
    entitlements = dict(PLAN_ENTITLEMENTS['FREE'])
    
    # Get active subscription
    subscription = get_active_subscription(chama)
    if subscription and subscription.plan:
        # Get plan entitlements
        plan_features = subscription.plan.features or {}
        entitlements.update(plan_features)
        entitlements['plan_code'] = subscription.plan.code
        entitlements['plan_name'] = subscription.plan.name
    else:
        entitlements['plan_code'] = Plan.FREE
        entitlements['plan_name'] = 'Free Trial'
    
    # Apply feature overrides
    overrides = FeatureOverride.objects.filter(
        chama=chama,
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )
    
    for override in overrides:
        entitlements[override.feature_key] = override.value
    
    # Cache results
    if use_cache:
        cache.set(cache_key, entitlements, ENTITLEMENTS_CACHE_TIMEOUT)
    
    return entitlements


def clear_entitlements_cache(chama):
    """Clear entitlements cache for a chama"""
    cache_key = f'entitlements_{chama.id}'
    cache.delete(cache_key)


def has_feature(chama, feature_key: str) -> bool:
    """
    Check if a chama has a specific feature
    """
    entitlements = get_entitlements(chama)
    return entitlements.get(feature_key, False)


def get_seat_usage(chama) -> int:
    """Get current seat usage for a chama"""
    from apps.chama.models import Membership

    current_count = Membership.objects.filter(
        chama=chama,
        status='active'
    ).count()
    SeatUsage.objects.update_or_create(
        chama=chama,
        defaults={'active_members_count': current_count},
    )
    return current_count


def check_seat_limit(chama) -> Dict[str, Any]:
    """
    Check if chama is within seat limit
    Returns dict with is_valid, current, limit, and available
    """
    entitlements = get_entitlements(chama)
    seat_limit = entitlements.get('seat_limit', 25)
    current_usage = get_seat_usage(chama)
    
    return {
        'is_valid': current_usage < seat_limit,
        'current': current_usage,
        'limit': seat_limit,
        'available': max(0, seat_limit - current_usage),
    }


def can_add_member(chama) -> bool:
    """Check if a new member can be added to the chama"""
    seat_info = check_seat_limit(chama)
    return seat_info['is_valid']


def get_plan_for_chama(chama) -> Optional[Plan]:
    """Get current plan for a chama"""
    subscription = get_active_subscription(chama)
    if subscription:
        return subscription.plan
    
    # Default to FREE
    return get_free_plan()


def create_subscription(
    chama,
    plan: Plan,
    provider: str = Subscription.MANUAL,
    billing_cycle: str = 'monthly',
    status: Optional[str] = None,
    period_start=None,
) -> Subscription:
    """Create a new subscription for a chama"""
    period_length = BILLING_CYCLE_DAYS.get(billing_cycle, BILLING_CYCLE_DAYS['monthly'])
    period_start = period_start or timezone.now()
    period_end = period_start + timedelta(days=period_length)
    rule = get_billing_rule(chama)
    resolved_status = status or (
        Subscription.TRIALING if plan.code == Plan.FREE else Subscription.ACTIVE
    )
    
    subscription = Subscription.objects.create(
        chama=chama,
        plan=plan,
        status=resolved_status,
        provider=provider,
        billing_cycle=billing_cycle,
        current_period_start=period_start,
        current_period_end=period_end,
        auto_renew=rule.auto_renew_enabled and plan.code != Plan.FREE,
        grace_period_ends_at=None,
        suspended_at=None,
    )
    
    # Log event
    BillingEvent.objects.create(
        chama=chama,
        event_type=BillingEvent.SUBSCRIPTION_CREATED,
        details={
            'plan': plan.code,
            'provider': provider,
            'subscription_id': str(subscription.id),
            'billing_cycle': billing_cycle,
        }
    )
    
    # Clear cache
    clear_entitlements_cache(chama)
    
    return subscription


def change_plan(
    chama,
    new_plan: Plan,
    performed_by=None,
    billing_cycle: str = 'monthly',
    provider: str = Subscription.MANUAL,
) -> Subscription:
    """Change subscription plan"""
    subscription = get_active_subscription(chama)
    period_length = BILLING_CYCLE_DAYS.get(billing_cycle, BILLING_CYCLE_DAYS['monthly'])
    period_start = timezone.now()
    period_end = period_start + timedelta(days=period_length)
    rule = get_billing_rule(chama)
    
    if subscription:
        old_plan_code = subscription.plan.code
        subscription.plan = new_plan
        subscription.status = Subscription.TRIALING if new_plan.code == Plan.FREE else Subscription.ACTIVE
        subscription.provider = provider or subscription.provider
        subscription.billing_cycle = billing_cycle
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end
        subscription.cancel_at_period_end = False
        subscription.auto_renew = rule.auto_renew_enabled and new_plan.code != Plan.FREE
        subscription.grace_period_ends_at = None
        subscription.suspended_at = None
        subscription.scheduled_plan = None
        subscription.scheduled_change_at = None
        subscription.failed_payment_count = 0
        subscription.save()
        
        # Log event
        BillingEvent.objects.create(
            chama=chama,
            event_type=BillingEvent.PLAN_CHANGED,
            details={
                'old_plan': old_plan_code,
                'new_plan': new_plan.code,
                'subscription_id': str(subscription.id),
                'billing_cycle': billing_cycle,
            },
            performed_by=performed_by,
        )
    else:
        # Create new subscription
        subscription = create_subscription(
            chama,
            new_plan,
            provider=provider,
            billing_cycle=billing_cycle,
        )
    
    # Update seat usage
    update_seat_usage(chama)
    
    # Clear cache
    clear_entitlements_cache(chama)
    
    return subscription


def confirm_checkout_subscription(
    chama,
    plan: Plan,
    *,
    billing_cycle: str = 'monthly',
    provider: str = Subscription.MANUAL,
    provider_subscription_id: Optional[str] = None,
    payment_reference: Optional[str] = None,
    payment_metadata: Optional[Dict[str, Any]] = None,
    invoice: Optional[Invoice] = None,
    charge_amount: Optional[Decimal] = None,
    performed_by=None,
) -> Subscription:
    """
    Activate a paid subscription only after checkout success has been confirmed.
    """
    if plan.code == Plan.FREE:
        raise ValueError('Checkout confirmation cannot activate the free trial plan.')

    if provider_subscription_id:
        existing_subscription = Subscription.objects.filter(
            chama=chama,
            provider_subscription_id=provider_subscription_id,
        ).select_related('plan').first()
        if existing_subscription:
            if invoice:
                mark_invoice_paid(
                    invoice,
                    payment_reference=payment_reference or provider_subscription_id,
                    provider_transaction_id=provider_subscription_id,
                )
            return existing_subscription

    if invoice is None:
        invoice = _build_subscription_invoice(
            chama=chama,
            plan=plan,
            provider=provider,
            billing_cycle=billing_cycle,
            customer_email=getattr(performed_by, 'email', '') or '',
            provider_transaction_id=provider_subscription_id or '',
            payment_metadata=payment_metadata
            or {
                'billing_cycle': billing_cycle,
                'provider': provider,
            },
            status=Invoice.PENDING,
        )['invoice']

    subscription = change_plan(
        chama,
        plan,
        performed_by=performed_by,
        billing_cycle=billing_cycle,
        provider=provider,
    )

    if provider_subscription_id:
        subscription.provider_subscription_id = provider_subscription_id
    if payment_reference:
        subscription.last_payment_reference = payment_reference
    if payment_metadata:
        subscription.payment_metadata = encrypt_billing_metadata(payment_metadata)
    subscription.failed_payment_count = 0
    subscription.grace_period_ends_at = None
    subscription.suspended_at = None
    subscription.last_invoiced_at = timezone.now()
    subscription.save(
        update_fields=[
            'provider_subscription_id',
            'last_payment_reference',
            'payment_metadata',
            'failed_payment_count',
            'grace_period_ends_at',
            'suspended_at',
            'last_invoiced_at',
            'updated_at',
        ]
    )

    if invoice.subscription_id != subscription.id:
        invoice.subscription = subscription
        invoice.save(update_fields=['subscription', 'updated_at'])
    mark_invoice_paid(
        invoice,
        payment_reference=payment_reference or provider_subscription_id or '',
        provider_transaction_id=provider_subscription_id or '',
    )

    generate_invoice_pdf(invoice)
    send_invoice_email(invoice)

    return subscription


def get_access_status(chama) -> Dict[str, Any]:
    """
    Resolve whether the chama can access protected dashboards.
    """
    latest_subscription = ensure_trial_subscription(chama)
    active_subscription = get_active_subscription(chama)
    now = timezone.now()
    rule = get_billing_rule(chama)

    trial_started_at = None
    trial_ends_at = None
    trial_days_remaining = 0

    if latest_subscription and latest_subscription.plan and latest_subscription.plan.code == Plan.FREE:
        trial_started_at = latest_subscription.current_period_start
        trial_ends_at = latest_subscription.current_period_end
        if trial_ends_at:
            trial_days_remaining = max(0, (trial_ends_at - now).days)

    if active_subscription:
        is_trialing = active_subscription.plan.code == Plan.FREE
        return {
            'granted': True,
            'requires_payment': False,
            'reason': 'trial_active' if is_trialing else 'subscription_active',
            'is_trialing': is_trialing,
            'trial_expired': False,
            'is_paid': not is_trialing,
            'in_grace_period': False,
            'grace_period_ends_at': None,
            'restricted_mode': False,
            'trial_days_remaining': trial_days_remaining if is_trialing else 0,
            'trial_started_at': trial_started_at.isoformat() if is_trialing and trial_started_at else None,
            'trial_ends_at': trial_ends_at.isoformat() if is_trialing and trial_ends_at else None,
        }

    if latest_subscription and latest_subscription.plan:
        if latest_subscription.plan.code == Plan.FREE:
            return {
                'granted': False,
                'requires_payment': True,
                'reason': 'trial_expired',
                'is_trialing': False,
                'trial_expired': True,
                'is_paid': False,
                'in_grace_period': False,
                'grace_period_ends_at': None,
                'restricted_mode': False,
                'trial_days_remaining': 0,
                'trial_started_at': trial_started_at.isoformat() if trial_started_at else None,
                'trial_ends_at': trial_ends_at.isoformat() if trial_ends_at else None,
            }

        grace_period_ends_at = latest_subscription.grace_period_ends_at
        if not grace_period_ends_at and latest_subscription.current_period_end:
            grace_period_ends_at = latest_subscription.current_period_end + timedelta(
                days=rule.grace_period_days
            )

        if grace_period_ends_at and grace_period_ends_at > now:
            soft_lock = rule.enforcement_mode == BillingRule.SOFT_LOCK
            return {
                'granted': soft_lock,
                'requires_payment': not soft_lock,
                'reason': 'grace_period_soft_lock' if soft_lock else 'grace_period_hard_lock',
                'is_trialing': False,
                'trial_expired': False,
                'is_paid': False,
                'in_grace_period': True,
                'grace_period_ends_at': grace_period_ends_at.isoformat(),
                'restricted_mode': soft_lock,
                'trial_days_remaining': 0,
                'trial_started_at': None,
                'trial_ends_at': None,
            }

        return {
            'granted': False,
            'requires_payment': True,
            'reason': 'subscription_expired',
            'is_trialing': False,
            'trial_expired': False,
            'is_paid': False,
            'in_grace_period': False,
            'grace_period_ends_at': grace_period_ends_at.isoformat()
            if grace_period_ends_at
            else None,
            'restricted_mode': False,
            'trial_days_remaining': 0,
            'trial_started_at': None,
            'trial_ends_at': None,
        }

    return {
        'granted': False,
        'requires_payment': True,
        'reason': 'subscription_required',
        'is_trialing': False,
        'trial_expired': False,
        'is_paid': False,
        'in_grace_period': False,
        'grace_period_ends_at': None,
        'restricted_mode': False,
        'trial_days_remaining': 0,
        'trial_started_at': None,
        'trial_ends_at': None,
    }


def cancel_subscription(chama, cancel_at_period_end: bool = True, performed_by=None) -> Subscription:
    """Cancel subscription"""
    subscription = get_active_subscription(chama)
    
    if subscription:
        subscription.cancel_at_period_end = cancel_at_period_end
        if cancel_at_period_end:
            subscription.status = Subscription.ACTIVE
        else:
            subscription.status = Subscription.CANCELED
            subscription.grace_period_ends_at = None
            subscription.suspended_at = timezone.now()
        
        subscription.save()
        
        # Log event
        BillingEvent.objects.create(
            chama=chama,
            event_type=BillingEvent.SUBSCRIPTION_CANCELED,
            details={
                'cancel_at_period_end': cancel_at_period_end,
                'subscription_id': str(subscription.id),
            },
            performed_by=performed_by,
        )
        
        # Clear cache
        clear_entitlements_cache(chama)
    
    return subscription


def update_seat_usage(chama):
    """Update seat usage for a chama"""
    current_count = get_seat_usage(chama)
    
    seat_usage, _ = SeatUsage.objects.update_or_create(
        chama=chama,
        defaults={
            'active_members_count': current_count,
        }
    )
    
    # Check for limit warning
    entitlements = get_entitlements(chama)
    seat_limit = entitlements.get('seat_limit', 25)
    
    if current_count >= seat_limit:
        # Log event
        BillingEvent.objects.get_or_create(
            chama=chama,
            event_type=BillingEvent.SEAT_LIMIT_EXCEEDED,
            defaults={
                'details': {
                    'current': current_count,
                    'limit': seat_limit,
                }
            }
        )
    elif current_count >= seat_limit * 0.9:
        # Warning at 90%
        BillingEvent.objects.get_or_create(
            chama=chama,
            event_type=BillingEvent.SEAT_LIMIT_WARNING,
            defaults={
                'details': {
                    'current': current_count,
                    'limit': seat_limit,
                    'percentage': round(current_count / seat_limit * 100),
                }
            }
        )

    sync_usage_limits(chama)
    
    return seat_usage


def get_billing_overview(user) -> Dict[str, Any]:
    """Get billing overview for a user (all their chamas)"""
    from apps.chama.models import Membership, MembershipRole, MemberStatus
    
    # Get user's chamas
    memberships = Membership.objects.filter(
        user=user,
        role__in=[
            MembershipRole.ADMIN,
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.SUPERADMIN,
            MembershipRole.TREASURER,
        ],
        status=MemberStatus.ACTIVE,
        is_active=True,
        is_approved=True,
    ).select_related('chama')
    
    chama_list = []
    for membership in memberships:
        chama = membership.chama
        plan = get_plan_for_chama(chama)
        subscription = get_active_subscription(chama)
        seats = check_seat_limit(chama)
        
        chama_list.append({
            'id': str(chama.id),
            'name': chama.name,
            'plan': {
                'code': plan.code if plan else 'FREE',
                'name': plan.name if plan else 'Free Trial',
            } if plan else None,
            'subscription': {
                'status': subscription.status if subscription else None,
                'current_period_end': subscription.current_period_end.isoformat() if subscription and subscription.current_period_end else None,
                'grace_period_ends_at': subscription.grace_period_ends_at.isoformat() if subscription and subscription.grace_period_ends_at else None,
            } if subscription else None,
            'seats': seats,
            'usage': sync_usage_limits(chama),
            'access': get_access_status(chama),
        })
    
    return {
        'chamas': chama_list,
    }


def preview_checkout_totals(chama, plan: Plan, billing_cycle: str) -> Dict[str, Any]:
    """Preview proration alongside any available billing credits."""
    proration = calculate_prorated_charge(chama, plan, billing_cycle)
    available_credit = get_available_credit_balance(chama)
    referral_credit = min(proration['charge_amount'], available_credit).quantize(Decimal('0.01'))
    net_charge = max(Decimal('0.00'), proration['charge_amount'] - referral_credit).quantize(
        Decimal('0.01')
    )
    return {
        'proration': proration,
        'available_credit': available_credit,
        'referral_credit_amount': referral_credit,
        'net_charge_amount': net_charge,
    }


def _build_subscription_invoice(
    *,
    chama,
    plan: Plan,
    provider: str,
    billing_cycle: str,
    customer_email: str = '',
    provider_transaction_id: str = '',
    payment_metadata: Optional[Dict[str, Any]] = None,
    status: str = Invoice.PENDING,
    preview: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    preview = preview or preview_checkout_totals(chama, plan, billing_cycle)
    proration = preview['proration']
    line_items = [
        {
            'title': f'{plan.name} subscription',
            'description': f'{billing_cycle.title()} access for {chama.name}',
            'quantity': 1,
            'unit_price': proration['full_amount'],
            'total_price': proration['full_amount'],
            'metadata': {'type': 'base_plan'},
        }
    ]
    if proration['prorated'] and proration['credit_amount'] > Decimal('0.00'):
        line_items.append(
            {
                'title': 'Unused time credit',
                'description': 'Automatic proration credit from the current paid plan',
                'quantity': 1,
                'unit_price': -proration['credit_amount'],
                'total_price': -proration['credit_amount'],
                'metadata': {'type': 'proration_credit'},
            }
        )

    invoice = create_invoice(
        chama=chama,
        plan=plan,
        amount=proration['charge_amount'],
        provider=provider,
        billing_cycle=billing_cycle,
        customer_email=customer_email,
        provider_transaction_id=provider_transaction_id,
        status=status,
        metadata=payment_metadata,
        line_items=line_items,
    )
    referral_credit_amount = reserve_credits_for_invoice(invoice, proration['charge_amount'])
    return {
        'invoice': invoice,
        'proration': proration,
        'referral_credit_amount': referral_credit_amount,
        'net_charge_amount': invoice.total_amount,
        'credit_summary': get_credit_summary(chama),
    }


def create_checkout_invoice(
    *,
    chama,
    plan: Plan,
    provider: str,
    billing_cycle: str,
    customer_email: str = '',
    provider_transaction_id: str = '',
    payment_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _build_subscription_invoice(
        chama=chama,
        plan=plan,
        provider=provider,
        billing_cycle=billing_cycle,
        customer_email=customer_email,
        provider_transaction_id=provider_transaction_id,
        payment_metadata=payment_metadata,
        status=Invoice.PENDING,
    )


def get_invoice_by_provider_reference(
    *,
    provider: str,
    provider_transaction_id: str,
) -> Optional[Invoice]:
    if not provider_transaction_id:
        return None
    return Invoice.objects.filter(
        provider=provider,
        provider_transaction_id=provider_transaction_id,
    ).order_by('-created_at').first()


def mark_invoice_payment_state(
    *,
    invoice: Optional[Invoice],
    paid: bool,
    payment_reference: str = '',
    provider_transaction_id: str = '',
) -> Optional[Invoice]:
    if not invoice:
        return None
    if paid:
        updated = mark_invoice_paid(
            invoice,
            payment_reference=payment_reference,
            provider_transaction_id=provider_transaction_id,
        )
        generate_invoice_pdf(updated)
        send_invoice_email(updated)
        return updated

    return mark_invoice_failed(invoice, payment_reference=payment_reference)


def get_usage_summary(chama) -> Dict[str, Any]:
    return sync_usage_limits(chama)


def _get_billing_contacts(chama) -> List[Any]:
    from apps.chama.models import MemberStatus, Membership, MembershipRole

    memberships = (
        Membership.objects.filter(
            chama=chama,
            role__in=[
                MembershipRole.ADMIN,
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.SUPERADMIN,
                MembershipRole.TREASURER,
            ],
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            user__is_active=True,
        )
        .select_related('user')
        .order_by('created_at')
    )

    contacts: List[Any] = []
    seen: set = set()
    for membership in memberships:
        if membership.user_id in seen:
            continue
        seen.add(membership.user_id)
        contacts.append(membership.user)
    return contacts


def _send_billing_notification(
    *,
    chama,
    subject: str,
    message: str,
    idempotency_prefix: str,
) -> int:
    from apps.notifications.models import NotificationPriority, NotificationType
    from apps.notifications.services import NotificationService

    sent = 0
    for user in _get_billing_contacts(chama):
        NotificationService.send_notification(
            user=user,
            message=message,
            channels=['email', 'in_app'],
            chama=chama,
            subject=subject,
            notification_type=NotificationType.SYSTEM,
            priority=NotificationPriority.HIGH,
            idempotency_key=f'{idempotency_prefix}:{user.id}',
        )
        sent += 1
    return sent


def send_renewal_reminders(now=None) -> int:
    now = now or timezone.now()
    cutoff = now + timedelta(days=7)
    sent = 0

    subscriptions = Subscription.objects.filter(
        status=Subscription.ACTIVE,
        current_period_end__isnull=False,
        current_period_end__gt=now,
        current_period_end__lte=cutoff,
    ).exclude(plan__code=Plan.FREE).select_related('chama', 'plan')

    for subscription in subscriptions:
        days_remaining = max(0, (subscription.current_period_end - now).days)
        renewal_mode = (
            'Auto-renew is enabled.'
            if subscription.auto_renew
            else 'Complete payment before expiry to avoid losing access.'
        )
        sent += _send_billing_notification(
            chama=subscription.chama,
            subject='Subscription renewal reminder',
            message=(
                f"Your {subscription.plan.name} subscription for {subscription.chama.name} "
                f"expires on {subscription.current_period_end.date().isoformat()} "
                f"({days_remaining} day{'s' if days_remaining != 1 else ''} remaining). "
                f"{renewal_mode}"
            ),
            idempotency_prefix=(
                f"billing:renewal:{subscription.id}:"
                f"{subscription.current_period_end.date().isoformat()}"
            ),
        )
    return sent


def send_failed_payment_reminders(now=None) -> int:
    now = now or timezone.now()
    sent = 0

    subscriptions = Subscription.objects.filter(
        status__in=[Subscription.PAST_DUE, Subscription.UNPAID],
        failed_payment_count__gt=0,
    ).exclude(plan__code=Plan.FREE).select_related('chama', 'plan')

    for subscription in subscriptions:
        if (
            subscription.grace_period_ends_at
            and subscription.grace_period_ends_at > now
        ):
            status_line = (
                "Your chama is in grace period until "
                f"{subscription.grace_period_ends_at.date().isoformat()}."
            )
        else:
            status_line = "Access is currently restricted until payment is confirmed."

        sent += _send_billing_notification(
            chama=subscription.chama,
            subject='Payment failed for your subscription',
            message=(
                f"A renewal payment for the {subscription.plan.name} plan failed "
                f"({subscription.failed_payment_count} attempt"
                f"{'s' if subscription.failed_payment_count != 1 else ''}). "
                f"{status_line}"
            ),
            idempotency_prefix=(
                f"billing:payment-failed:{subscription.id}:"
                f"{subscription.failed_payment_count}"
            ),
        )
    return sent


def send_credit_expiry_reminders(now=None) -> int:
    now = now or timezone.now()
    cutoff = now + timedelta(days=7)
    sent = 0

    credits = BillingCredit.objects.filter(
        remaining_amount__gt=Decimal('0.00'),
        expires_at__isnull=False,
        expires_at__gt=now,
        expires_at__lte=cutoff,
    ).select_related('chama').order_by('expires_at', 'created_at')

    for credit in credits:
        days_remaining = max(0, (credit.expires_at - now).days)
        sent += _send_billing_notification(
            chama=credit.chama,
            subject='Billing credit expiring soon',
            message=(
                f"Your {credit.currency} {credit.remaining_amount:,.2f} billing credit "
                f"for {credit.chama.name} expires on {credit.expires_at.date().isoformat()} "
                f"({days_remaining} day{'s' if days_remaining != 1 else ''} remaining). "
                "Use it before it expires."
            ),
            idempotency_prefix=(
                f"billing:credit-expiry:{credit.id}:"
                f"{credit.expires_at.date().isoformat()}"
            ),
        )
    return sent


def process_subscription_lifecycle(now=None) -> Dict[str, int]:
    now = now or timezone.now()
    applied_changes = apply_due_scheduled_changes(now)
    marked_grace = 0
    suspended = 0

    expired_subscriptions = Subscription.objects.filter(
        status__in=[Subscription.ACTIVE, Subscription.TRIALING],
        current_period_end__isnull=False,
        current_period_end__lte=now,
    ).select_related('chama', 'plan')

    for subscription in expired_subscriptions:
        if subscription.plan.code == Plan.FREE:
            continue

        rule = get_billing_rule(subscription.chama)
        grace_end = subscription.grace_period_ends_at or (
            subscription.current_period_end + timedelta(days=rule.grace_period_days)
        )

        if grace_end > now:
            if subscription.grace_period_ends_at != grace_end:
                subscription.grace_period_ends_at = grace_end
                subscription.status = Subscription.PAST_DUE
                subscription.save(
                    update_fields=['grace_period_ends_at', 'status', 'updated_at']
                )
                marked_grace += 1
            continue

        if subscription.status != Subscription.UNPAID:
            subscription.status = Subscription.UNPAID
            subscription.suspended_at = now
            subscription.save(update_fields=['status', 'suspended_at', 'updated_at'])
            suspended += 1

    return {
        'scheduled_changes_applied': applied_changes,
        'grace_marked': marked_grace,
        'suspended': suspended,
    }


def process_payment_retries(now=None) -> int:
    now = now or timezone.now()
    processed = 0
    for subscription in Subscription.objects.filter(
        status=Subscription.PAST_DUE,
        auto_renew=True,
        grace_period_ends_at__gt=now,
    ).select_related('chama', 'plan'):
        metadata = decrypt_billing_metadata(subscription.payment_metadata)
        phone = metadata.get('phone')

        if subscription.provider == Subscription.MPESA and phone:
            from .payments import PaymentProviderFactory

            usage = usage_within_limit(subscription.chama, UsageMetric.STK_PUSHES, 1)
            if not usage['allowed']:
                BillingEvent.objects.create(
                    chama=subscription.chama,
                    event_type=BillingEvent.PAYMENT_FAILED,
                    details={
                        'reason': 'billing_stk_limit_exceeded',
                        'current': usage['current'],
                        'limit': usage['limit'],
                        'subscription_id': str(subscription.id),
                    },
                )
                subscription.failed_payment_count += 1
                subscription.save(update_fields=['failed_payment_count', 'updated_at'])
                processed += 1
                continue

            amount = calculate_plan_amount(
                subscription.plan,
                subscription.billing_cycle or Subscription.MONTHLY,
            )
            retry_result = PaymentProviderFactory.create_checkout(
                provider_id='mpesa',
                plan_id=subscription.plan_id,
                plan_name=subscription.plan.name,
                amount=amount,
                currency='KES',
                billing_cycle=subscription.billing_cycle or Subscription.MONTHLY,
                customer_email='',
                customer_phone=phone,
                chama_id=str(subscription.chama_id),
                success_url=f"{getattr(settings, 'SITE_URL', 'http://localhost:8000')}/billing/success",
                cancel_url=f"{getattr(settings, 'SITE_URL', 'http://localhost:8000')}/billing/cancel",
            )
            if retry_result.success:
                increment_usage(subscription.chama, 'stk_pushes', 1)
                create_checkout_invoice(
                    chama=subscription.chama,
                    plan=subscription.plan,
                    provider=Subscription.MPESA,
                    billing_cycle=subscription.billing_cycle or Subscription.MONTHLY,
                    customer_email='',
                    provider_transaction_id=retry_result.transaction_id or '',
                    payment_metadata=metadata,
                )
        subscription.failed_payment_count += 1
        subscription.save(update_fields=['failed_payment_count', 'updated_at'])
        processed += 1
    return processed


def reset_usage_cycles(now=None) -> int:
    return reset_due_usage_metrics(now=now)


def cleanup_credit_reservations() -> Dict[str, Any]:
    return release_stale_credit_reservations_for_all()


def get_admin_billing_dashboard() -> Dict[str, Any]:
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    active_subscriptions = Subscription.objects.filter(
        status__in=[Subscription.ACTIVE, Subscription.TRIALING],
    ).select_related('plan')
    expiring_soon = active_subscriptions.filter(
        current_period_end__isnull=False,
        current_period_end__lte=now + timedelta(days=7),
    ).count()
    invoices_this_month = Invoice.objects.filter(created_at__gte=month_start)
    paid_invoices_this_month = invoices_this_month.filter(status=Invoice.PAID)

    mrr = Decimal('0.00')
    for subscription in active_subscriptions:
        if subscription.plan.code == Plan.FREE:
            continue
        if subscription.billing_cycle == Subscription.YEARLY:
            mrr += (subscription.plan.yearly_price / Decimal('12')).quantize(Decimal('0.01'))
        else:
            mrr += Decimal(subscription.plan.monthly_price)

    plan_distribution = list(
        active_subscriptions.values('plan__code', 'plan__name').annotate(total=Count('id'))
    )

    top_usage = []
    for invoice in Invoice.objects.filter(status=Invoice.PAID).select_related('chama', 'plan')[:20]:
        top_usage.append(
            {
                'chama_id': str(invoice.chama_id),
                'chama_name': invoice.chama.name,
                'plan': invoice.plan.code,
                'amount': float(invoice.total_amount),
            }
        )

    recent_logs = list(
        BillingEvent.objects.filter(
            event_type__in=[
                BillingEvent.PLAN_CHANGED,
                BillingEvent.SUBSCRIPTION_CREATED,
                BillingEvent.PAYMENT_SUCCEEDED,
                BillingEvent.PAYMENT_FAILED,
            ]
        )
        .order_by('-created_at')[:20]
        .values('event_type', 'details', 'created_at')
    )
    credit_summary = get_credit_admin_summary()

    return {
        'active_subscriptions': active_subscriptions.count(),
        'expiring_soon': expiring_soon,
        'revenue_this_month': float(
            paid_invoices_this_month.aggregate(total=Sum('total_amount')).get('total')
            or Decimal('0.00')
        ),
        'mrr': float(mrr),
        'failed_payments': invoices_this_month.filter(status=Invoice.FAILED).count(),
        'plan_distribution': plan_distribution,
        'recent_changes': recent_logs,
        'credits': credit_summary,
        'usage_analytics': {
            'invoices_sample': top_usage,
        },
    }
