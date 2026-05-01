"""
Wizard API views for chama setup onboarding.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    Chama,
    ChamaSettings,
    ContributionPlan,
    ExpensePolicy,
    LoanPolicy,
    Membership,
    MembershipRole,
    MemberStatus,
    PaymentProviderConfig,
)


def _get_wizard_membership(request):
    """
    Resolve the active membership used by the onboarding wizard.

    The creator may choose a non-admin role during step 1, so the wizard must
    not lock them out after the group is created.
    """
    memberships = (
        Membership.objects.select_related("chama")
        .filter(
            user=request.user,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        )
    )

    scoped_chama_id = request.headers.get("X-CHAMA-ID") or request.META.get("HTTP_X_CHAMA_ID")
    if scoped_chama_id:
        scoped_membership = memberships.filter(chama_id=scoped_chama_id).order_by("-joined_at").first()
        if scoped_membership:
            return scoped_membership

    in_progress_membership = (
        memberships.filter(chama__setup_completed=False)
        .order_by("-updated_at", "-joined_at")
        .first()
    )
    if in_progress_membership:
        return in_progress_membership

    return memberships.order_by("-updated_at", "-joined_at").first()


def _completion_redirect(role: str) -> str:
    redirects = {
        MembershipRole.TREASURER: "/treasurer/dashboard",
        MembershipRole.SECRETARY: "/secretary/dashboard",
        MembershipRole.AUDITOR: "/auditor/dashboard",
        MembershipRole.MEMBER: "/member/dashboard",
    }
    return redirects.get(role, "/admin/dashboard")


def _resolve_referrer(*, request, referral_enabled, referral_code):
    normalized_code = str(referral_code or "").strip().upper()
    referral_requested = bool(referral_enabled) or bool(normalized_code)

    if not referral_requested:
        return None, None

    if not normalized_code:
        return "A referral code is required when referral is enabled.", None

    user_model = get_user_model()
    referrer = user_model.objects.filter(referral_code=normalized_code).first()
    if not referrer:
        return "The referral code you entered is invalid.", None

    if referrer.id == request.user.id:
        return "You cannot use your own referral code.", None

    return None, referrer


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def wizard_status(request):
    """Get current wizard progress."""
    membership = _get_wizard_membership(request)
    
    if not membership:
        return Response({
            'has_chama': False,
            'setup_completed': False,
            'current_step': 0
        })
    
    chama = membership.chama
    return Response({
        'has_chama': True,
        'chama_id': str(chama.id),
        'chama_name': chama.name,
        'setup_completed': chama.setup_completed,
        'current_step': chama.setup_step,
        'chama_type': chama.chama_type,
    })


@api_view(['GET', 'POST', 'PUT'])
@permission_classes([IsAuthenticated])
def group_setup(request):
    """Step 1: Group Setup Details."""
    membership = _get_wizard_membership(request)
    
    if request.method == 'GET':
        if not membership:
            return Response({'exists': False})
        
        chama = membership.chama
        getattr(chama, 'settings', None)
        
        return Response({
            'exists': True,
            'organization_name': chama.name,
            'member_count': chama.max_members,
            'group_type': chama.chama_type,
            'user_role': membership.role,
            'country': chama.county,
            'currency': chama.currency,
            'is_registered_entity': False,  # Would need field added
            'registration_number': None,
            'referral_enabled': bool(chama.referral_code_used),
            'referral_code': chama.referral_code_used or '',
        })
    
    # POST/PUT - Create or update group
    data = request.data
    organization_name = data.get('organization_name')
    member_count = int(data.get('member_count', 10))
    group_type = data.get('group_type', 'savings')
    user_role = data.get('user_role', 'ADMIN')
    country = data.get('country', 'Kenya')
    currency = data.get('currency', 'KES')
    referral_enabled = data.get('referral_enabled', False)
    referral_code = data.get('referral_code', '')

    referral_error, referrer = _resolve_referrer(
        request=request,
        referral_enabled=referral_enabled,
        referral_code=referral_code,
    )
    if referral_error:
        return Response({'detail': referral_error}, status=status.HTTP_400_BAD_REQUEST)
    normalized_referral_code = referrer.referral_code if referrer else ''
    
    with transaction.atomic():
        if not membership:
            # Create new chama
            chama = Chama.objects.create(
                name=organization_name,
                max_members=member_count,
                chama_type=group_type,
                county=country,
                currency=currency,
                referred_by=referrer,
                referral_code_used=normalized_referral_code,
                referral_applied_at=timezone.now() if referrer else None,
                setup_step=1,
                created_by=request.user,
                updated_by=request.user,
            )
            
            # Create admin membership
            membership = Membership.objects.create(
                user=request.user,
                chama=chama,
                role=user_role,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                approved_by=request.user,
                approved_at=timezone.now(),
                created_by=request.user,
                updated_by=request.user,
            )
            
            # Create default settings
            ChamaSettings.objects.create(
                chama=chama,
                created_by=request.user,
                updated_by=request.user,
            )
            LoanPolicy.objects.create(
                chama=chama,
                created_by=request.user,
                updated_by=request.user,
            )
            ExpensePolicy.objects.create(
                chama=chama,
                created_by=request.user,
                updated_by=request.user,
            )
        else:
            # Update existing chama
            chama = membership.chama
            chama.name = organization_name
            chama.max_members = member_count
            chama.chama_type = group_type
            chama.county = country
            chama.currency = currency
            chama.referred_by = referrer
            chama.referral_code_used = normalized_referral_code
            chama.referral_applied_at = timezone.now() if referrer else None
            chama.setup_step = 1
            chama.updated_by = request.user
            chama.save()
            
            membership.role = user_role
            membership.updated_by = request.user
            membership.save()
    
    return Response({
        'success': True,
        'chama_id': str(chama.id),
        'next_step': '/onboarding/wizard/add-members'
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def add_members(request):
    """Step 2: Add Members."""
    membership = _get_wizard_membership(request)
    
    if not membership:
        return Response({'error': 'No chama found'}, status=status.HTTP_404_NOT_FOUND)
    
    chama = membership.chama
    
    if request.method == 'GET':
        # Get pending invites/members
        from .models import Invite, InviteLink
        invites = Invite.objects.filter(chama=chama)[:10]
        invite_links = InviteLink.objects.filter(chama=chama, is_active=True)
        
        return Response({
            'members': [],
            'invites': [
                {
                    'id': str(i.id),
                    'phone': i.phone,
                    'role': i.role,
                    'status': i.status,
                    'expires_at': i.expires_at.isoformat() if i.expires_at else None,
                }
                for i in invites
            ],
            'invite_links': [
                {
                    'id': str(l.id),
                    'code': l.code,
                    'role': l.role,
                    'uses': l.use_count,
                    'max_uses': l.max_uses,
                    'expires_at': l.expires_at.isoformat() if l.expires_at else None,
                }
                for l in invite_links
            ]
        })
    
    # POST - Add members/invites
    data = request.data
    members = data.get('members', [])
    invite_links = data.get('invite_links', [])
    
    with transaction.atomic():
        # Create invites
        for member_data in members:
            from .models import Invite
            Invite.objects.create(
                chama=chama,
                identifier=member_data.get('phone') or member_data.get('email') or 'member-invite',
                token=Invite.generate_token(),
                phone=member_data.get('phone'),
                email=member_data.get('email', ''),
                role=member_data.get('role', 'MEMBER'),
                invited_by=request.user,
                expires_at=timezone.now() + timezone.timedelta(days=7),
                created_by=request.user,
                updated_by=request.user,
            )
        
        # Create invite links
        for link_data in invite_links:
            from .models import InviteLink
            InviteLink.objects.create(
                chama=chama,
                created_by=request.user,
                max_uses=link_data.get('max_uses'),
                expires_at=timezone.now() + timezone.timedelta(days=30),
                preassigned_role=link_data.get('role', ''),
                is_active=True,
                updated_by=request.user,
            )
        
        # Update step
        chama.setup_step = 2
        chama.save()
    
    return Response({
        'success': True,
        'next_step': '/onboarding/wizard/contribution-setup'
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def contribution_setup(request):
    """Step 3: Contribution Setup."""
    membership = _get_wizard_membership(request)
    
    if not membership:
        return Response({'error': 'No chama found'}, status=status.HTTP_404_NOT_FOUND)
    
    chama = membership.chama
    
    if request.method == 'GET':
        plans = ContributionPlan.objects.filter(chama=chama)
        return Response({
            'contributions': [
                {
                    'id': str(p.id),
                    'name': p.name,
                    'description': p.description,
                    'type': p.contribution_type,
                    'amount': float(p.fixed_amount) if p.fixed_amount else None,
                    'min_amount': float(p.min_amount),
                    'max_amount': float(p.max_amount),
                    'frequency': p.frequency,
                    'due_day': p.due_day,
                    'is_default': p.is_default,
                }
                for p in plans
            ],
            'settings': {}
        })
    
    # POST - Save contribution plans
    data = request.data
    contributions = data.get('contributions', [])
    
    with transaction.atomic():
        # Clear existing plans
        ContributionPlan.objects.filter(chama=chama).delete()
        
        # Create new plans
        for contribution in contributions:
            ContributionPlan.objects.create(
                chama=chama,
                name=contribution.get('name', 'Main Contribution'),
                description=contribution.get('description', ''),
                contribution_type=contribution.get('type', 'fixed'),
                fixed_amount=contribution.get('amount', 0),
                min_amount=contribution.get('min_amount', 0),
                max_amount=contribution.get('max_amount', 0),
                frequency=contribution.get('frequency', 'weekly'),
                due_day=contribution.get('due_day', 5),
                is_default=contribution.get('is_default', True),
            )
        
        # Update settings
        settings = getattr(chama, 'settings', None)
        if settings:
            settings.grace_period_days = data.get('grace_period_days', 2)
            settings.late_penalty_type = data.get('late_penalty_type', 'flat')
            settings.late_penalty_amount = data.get('late_penalty_amount', 0)
            settings.save()
        
        # Update step
        chama.setup_step = 3
        chama.save()
    
    return Response({
        'success': True,
        'next_step': '/onboarding/wizard/loan-types'
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def loan_types(request):
    """Step 4: Loan Types."""
    membership = _get_wizard_membership(request)
    
    if not membership:
        return Response({'error': 'No chama found'}, status=status.HTTP_404_NOT_FOUND)
    
    chama = membership.chama
    
    if request.method == 'GET':
        loan_policy = getattr(chama, 'loan_policy', None)
        if loan_policy:
            return Response({
                'loans_enabled': loan_policy.loans_enabled,
                'min_contribution_cycles': loan_policy.min_contribution_cycles,
                'max_active_loans': loan_policy.max_active_loans,
                'loan_cap_multiplier': float(loan_policy.loan_cap_multiplier),
                'interest_model': loan_policy.interest_model,
                'interest_rate': float(loan_policy.interest_rate),
                'require_guarantors': loan_policy.require_guarantors,
                'min_guarantors': loan_policy.min_guarantors,
                'require_treasurer_approval': loan_policy.require_treasurer_approval,
                'require_admin_approval': loan_policy.require_admin_approval,
                'penalty_rate': float(loan_policy.penalty_rate),
                'max_repayment_period': loan_policy.max_repayment_period,
                'allow_early_repayment': loan_policy.allow_early_repayment,
            })
        return Response({'loans_enabled': True})
    
    # POST - Save loan policy
    data = request.data
    
    with transaction.atomic():
        loan_policy, _ = LoanPolicy.objects.update_or_create(
            chama=chama,
            defaults={
                'loans_enabled': data.get('loans_enabled', True),
                'min_contribution_cycles': data.get('min_contribution_cycles', 3),
                'max_active_loans': data.get('max_active_loans', 1),
                'loan_cap_multiplier': data.get('loan_cap_multiplier', 3.0),
                'interest_model': data.get('interest_model', 'flat'),
                'interest_rate': data.get('interest_rate', 10),
                'require_guarantors': data.get('require_guarantors', True),
                'min_guarantors': data.get('min_guarantors', 1),
                'require_treasurer_approval': data.get('require_treasurer_approval', True),
                'require_admin_approval': data.get('require_admin_approval', True),
                'penalty_rate': data.get('penalty_rate', 2),
                'max_repayment_period': data.get('max_repayment_period', 12),
                'allow_early_repayment': data.get('allow_early_repayment', True),
            }
        )
        
        # Update step
        chama.setup_step = 4
        chama.save()
    
    return Response({
        'success': True,
        'next_step': '/onboarding/wizard/bank-setup'
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def bank_setup(request):
    """Step 5: Bank Account Setup."""
    membership = _get_wizard_membership(request)
    
    if not membership:
        return Response({'error': 'No chama found'}, status=status.HTTP_404_NOT_FOUND)
    
    chama = membership.chama
    
    if request.method == 'GET':
        configs = PaymentProviderConfig.objects.filter(chama=chama)
        return Response({
            'providers': [
                {
                    'id': str(c.id),
                    'type': c.provider_type,
                    'mpesa_shortcode': c.mpesa_shortcode,
                    'bank_name': c.bank_name,
                    'bank_account_number': c.bank_account_number,
                    'bank_branch': c.bank_branch,
                    'allow_manual_entry': c.allow_manual_entry,
                    'is_active': c.is_active,
                }
                for c in configs
            ]
        })
    
    # POST - Save payment providers
    data = request.data
    providers = data.get('providers', [])
    
    with transaction.atomic():
        # Clear existing configs
        PaymentProviderConfig.objects.filter(chama=chama).delete()
        
        # Create new configs
        for provider in providers:
            PaymentProviderConfig.objects.create(
                chama=chama,
                provider_type=provider.get('type', 'manual'),
                mpesa_shortcode=provider.get('mpesa_shortcode', ''),
                bank_name=provider.get('bank_name', ''),
                bank_account_number=provider.get('bank_account_number', ''),
                bank_branch=provider.get('bank_branch', ''),
                allow_manual_entry=provider.get('allow_manual_entry', True),
                is_active=provider.get('is_active', True),
            )
        
        # Update step
        chama.setup_step = 5
        chama.updated_by = request.user
        chama.save()
    
    return Response({
        'success': True,
        'next_step': '/onboarding/wizard/confirmation'
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete_wizard(request):
    """Step 6: Complete wizard."""
    membership = _get_wizard_membership(request)
    
    if not membership:
        return Response({'error': 'No chama found'}, status=status.HTTP_404_NOT_FOUND)
    
    chama = membership.chama
    chama.setup_completed = True
    chama.setup_step = 6
    chama.updated_by = request.user
    chama.generate_join_code()  # Generate invite code
    chama.save()

    from apps.accounts.referrals import award_referral_reward_for_completed_chama

    award_referral_reward_for_completed_chama(chama)
    
    return Response({
        'success': True,
        'chama_id': str(chama.id),
        'chama_name': chama.name,
        'join_code': chama.join_code,
        'redirect': _completion_redirect(membership.role)
    })
