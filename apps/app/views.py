"""
Unified App API Views - Integration layer for Flutter mobile app
Combines data from all other apps into a single API surface
"""

from datetime import timedelta
from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from apps.accounts.models import User
from apps.billing.gating import require_billing_access, require_feature
from apps.chama.models import Chama, Membership
from apps.finance.models import (
    Contribution, 
    ContributionGoal, 
    ContributionSchedule, 
    Wallet, 
    LedgerEntry,
    Loan
)
from apps.payments.models import PaymentIntent, MpesaSTKTransaction, MpesaB2CPayout
from apps.notifications.models import Notification
from core.algorithms.membership import (
    compute_effective_role,
    calculate_loan_eligibility,
    calculate_compliance,
)

# Wallet API constants
CURRENCY = 'KES'
MIN_DEPOSIT = 10  # KES
MAX_DEPOSIT = 150000  # KES
MIN_WITHDRAWAL = 100  # KES
MAX_WITHDRAWAL = 50000  # KES
DAILY_WITHDRAWAL_LIMIT = 150000  # KES
WITHDRAWAL_COOLDOWN_MINUTES = 5


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def dashboard_summary(request):
    """
    Get comprehensive dashboard summary for the authenticated user.
    Integrates data from: accounts, chama, finance, payments, notifications
    """
    try:
        user = request.user
        memberships = Membership.objects.filter(
            user=user,
            status='ACTIVE'
        ).select_related('chama')
        
        chama_ids = [m.chama_id for m in memberships]
        
        # Get wallet balances
        wallets = Wallet.objects.filter(
            Q(owner_type='USER', owner_id=user.id) |
            Q(chama_id__in=chama_ids)
        )
        
        total_available = sum(w.available_balance for w in wallets)
        total_locked = sum(w.locked_balance for w in wallets)
        
        # Get recent contributions
        recent_contributions = Contribution.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:5]
        
        # Get active loans
        active_loans = Loan.objects.filter(
            member=user,
            status__in=['ACTIVE', 'DISBURSED']
        )
        
        # Get unread notifications
        unread_notifications = Notification.objects.filter(
            recipient=user,
            is_read=False
        ).count()
        
        # Calculate compliance score
        compliance = calculate_compliance(str(user.id))
        
        return Response({
            'user': {
                'id': str(user.id),
                'name': user.get_full_name() or user.phone,
                'phone': user.phone,
            },
            'wallets': {
                'total_available': float(total_available),
                'total_locked': float(total_locked),
                'total': float(total_available + total_locked),
            },
            'memberships': {
                'count': memberships.count(),
                'chamas': [
                    {
                        'id': str(m.chama.id),
                        'name': m.chama.name,
                        'role': m.role,
                        'effective_role': compute_effective_role(m, None)[0],
                    }
                    for m in memberships[:5]
                ]
            },
            'contributions': {
                'recent_count': recent_contributions.count(),
                'recent': [
                    {
                        'id': str(c.id),
                        'amount': float(c.amount),
                        'chama': c.chama.name,
                        'date': c.created_at.isoformat(),
                    }
                    for c in recent_contributions
                ]
            },
            'loans': {
                'active_count': active_loans.count(),
                'total_outstanding': float(
                    sum(l.balance_remaining for l in active_loans)
                ),
            },
            'notifications': {
                'unread_count': unread_notifications,
            },
            'compliance': compliance,
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def chama_detail(request, chama_id):
    """
    Get detailed information about a specific chama.
    Integrates: chama, finance, payments, loans, meetings
    """
    try:
        user = request.user
        
        # Verify membership
        membership = Membership.objects.filter(
            user=user,
            chama_id=chama_id,
            status='ACTIVE'
        ).first()
        
        if not membership:
            return Response(
                {'error': 'Not a member of this chama'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        chama = membership.chama
        
        # Get members
        members = Membership.objects.filter(
            chama=chama,
            status='ACTIVE'
        ).select_related('user')
        
        # Get wallet
        wallet = Wallet.objects.filter(
            chama=chama
        ).first()
        
        # Get recent transactions
        recent_transactions = LedgerEntry.objects.filter(
            chama=chama
        ).order_by('-created_at')[:10]
        
        # Get active loans
        active_loans = Loan.objects.filter(
            chama=chama,
            status__in=['ACTIVE', 'DISBURSED']
        ).select_related('member')
        
        # Get contribution totals
        contribution_totals = Contribution.objects.filter(
            chama=chama
        ).aggregate(
            total=Sum('amount'),
            count=Count('id')
        )
        
        return Response({
            'chama': {
                'id': str(chama.id),
                'name': chama.name,
                'description': chama.description,
                'created_at': chama.created_at.isoformat(),
            },
            'membership': {
                'role': membership.role,
                'effective_role': compute_effective_role(membership, None)[0],
                'joined_at': membership.joined_at.isoformat(),
            },
            'wallet': {
                'available': float(wallet.available_balance) if wallet else 0,
                'locked': float(wallet.locked_balance) if wallet else 0,
                'total': float(wallet.total_balance()) if wallet else 0,
            } if wallet else None,
            'members': {
                'count': members.count(),
                'list': [
                    {
                        'id': str(m.user.id),
                        'name': m.user.get_full_name() or m.user.phone,
                        'role': m.role,
                    }
                    for m in members
                ]
            },
            'transactions': {
                'recent': [
                    {
                        'id': str(t.id),
                        'type': t.entry_type,
                        'amount': float(t.amount),
                        'direction': t.direction,
                        'date': t.created_at.isoformat(),
                    }
                    for t in recent_transactions
                ]
            },
            'loans': {
                'active_count': active_loans.count(),
                'total_outstanding': float(
                    sum(l.balance_remaining for l in active_loans)
                ),
                'list': [
                    {
                        'id': str(l.id),
                        'member': l.member.get_full_name() or l.member.phone,
                        'principal': float(l.principal),
                        'balance': float(l.balance_remaining),
                        'status': l.status,
                    }
                    for l in active_loans[:5]
                ]
            },
            'contributions': {
                'total': float(contribution_totals['total'] or 0),
                'count': contribution_totals['count'] or 0,
            }
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('full_finance_management')
def loan_detail(request, loan_id):
    """
    Get detailed loan information including repayment schedule.
    """
    try:
        user = request.user
        
        loan = Loan.objects.filter(
            id=loan_id,
            member=user
        ).first()
        
        if not loan:
            return Response(
                {'error': 'Loan not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Calculate eligibility for                status=status.HTTP new loans
        membership = Membership.objects.filter(
            user=user,
            chama=loan.chama,
            status='ACTIVE'
        ).first()
        
        eligibility = None
        if membership:
            eligibility = calculate_loan_eligibility(membership)
        
        return Response({
            'loan': {
                'id': str(loan.id),
                'chama': {
                    'id': str(loan.chama.id),
                    'name': loan.chama.name,
                },
                'principal': float(loan.principal),
                'interest_rate': float(loan.interest_rate),
                'balance_remaining': float(loan.balance_remaining),
                'status': loan.status,
                'disbursed_at': loan.disbursed_at.isoformat() if loan.disbursed_at else None,
                'due_date': loan.due_date.isoformat() if loan.due_date else None,
                'repayment_amount': float(loan.repayment_amount),
            },
            'eligibility': eligibility,
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('mpesa_stk')
def payment_history(request):
    """
    Get payment history for the user across all chamas.
    Combines STK transactions, B2C payouts, and ledger entries.
    """
    try:
        user = request.user
        
        # Get membership chama IDs
        memberships = Membership.objects.filter(
            user=user,
            status='ACTIVE'
        )
        chama_ids = [m.chama_id for m in memberships]
        
        # Get STK transactions
        stk_transactions = MpesaSTKTransaction.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:20]
        
        # Get B2C payouts
        b2c_payouts = MpesaB2CPayout.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:20]
        
        # Get payment intents
        payment_intents = PaymentIntent.objects.filter(
            chama_id__in=chama_ids
        ).order_by('-created_at')[:20]
        
        return Response({
            'stk_transactions': [
                {
                    'id': str(t.id),
                    'amount': float(t.amount),
                    'phone': t.phone,
                    'status': t.status,
                    'receipt': t.mpesa_receipt_number,
                    'date': t.created_at.isoformat(),
                }
                for t in stk_transactions
            ],
            'b2c_payouts': [
                {
                    'id': str(p.id),
                    'amount': float(p.amount),
                    'phone': p.phone_number,
                    'status': p.status,
                    'date': p.created_at.isoformat(),
                }
                for p in b2c_payouts
            ],
            'payment_intents': [
                {
                    'id': str(i.id),
                    'amount': float(i.amount),
                    'type': i.intent_type,
                    'status': i.status,
                    'date': i.created_at.isoformat(),
                }
                for i in payment_intents
            ],
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_billing_access()
def member_profile(request):
    """
    Get comprehensive member profile including all memberships and stats.
    """
    try:
        user = request.user
        
        # Get all memberships
        memberships = Membership.objects.filter(
            user=user
        ).select_related('chama')
        
        # Get wallet
        wallet = Wallet.objects.filter(
            owner_type='USER',
            owner_id=user.id
        ).first()
        
        # Get loans
        loans = Loan.objects.filter(member=user)
        active_loans = loans.filter(status__in=['ACTIVE', 'DISBURSED'])
        completed_loans = loans.filter(status='PAID')
        
        # Calculate totals
        total_contributed = Contribution.objects.filter(
            member=user
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        return Response({
            'user': {
                'id': str(user.id),
                'name': user.get_full_name(),
                'phone': user.phone,
                'email': user.email,
                'date_joined': user.date_joined.isoformat(),
            },
            'wallet': {
                'available': float(wallet.available_balance) if wallet else 0,
                'locked': float(wallet.locked_balance) if wallet else 0,
                'total': float(wallet.total_balance()) if wallet else 0,
            } if wallet else None,
            'memberships': {
                'total': memberships.count(),
                'active': memberships.filter(status='ACTIVE').count(),
                'list': [
                    {
                        'chama': {
                            'id': str(m.chama.id),
                            'name': m.chama.name,
                        },
                        'role': m.role,
                        'status': m.status,
                        'joined_at': m.joined_at.isoformat(),
                    }
                    for m in memberships
                ]
            },
            'loans': {
                'total': loans.count(),
                'active': active_loans.count(),
                'completed': completed_loans.count(),
                'total_outstanding': float(
                    sum(l.balance_remaining for l in active_loans)
                ),
            },
            'contributions': {
                'total': float(total_contributed),
            }
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ========================================================================
# WALLET API ENDPOINTS (per requirements)
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('contributions_basic')
def wallet_info(request):
    """
    GET /api/app/wallet/
    Get user's wallet details with balances.
    """
    try:
        user = request.user
        
        # Get user's wallet
        wallet = Wallet.objects.filter(
            owner_type='USER',
            owner_id=user.id
        ).first()
        
        # Get recent ledger entries
        recent_ledger = LedgerEntry.objects.filter(
            wallet__owner_type='USER',
            wallet__owner_id=user.id
        ).order_by('-created_at')[:10]
        
        # Calculate today's withdrawal total
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_withdrawals = LedgerEntry.objects.filter(
            wallet__owner_type='USER',
            wallet__owner_id=user.id,
            entry_type='WITHDRAWAL',
            direction='debit',
            status='success',
            created_at__gte=today_start
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        return Response({
            'wallet': {
                'id': str(wallet.id) if wallet else None,
                'available_balance': float(wallet.available_balance) if wallet else 0,
                'locked_balance': float(wallet.locked_balance) if wallet else 0,
                'total_balance': float(wallet.total_balance()) if wallet else 0,
                'currency': CURRENCY,
            },
            'limits': {
                'min_deposit': MIN_DEPOSIT,
                'max_deposit': MAX_DEPOSIT,
                'min_withdrawal': MIN_WITHDRAWAL,
                'max_withdrawal': MAX_WITHDRAWAL,
                'daily_withdrawal_limit': DAILY_WITHDRAWAL_LIMIT,
                'today_withdrawn': float(today_withdrawals),
                'remaining_daily': float(DAILY_WITHDRAWAL_LIMIT - today_withdrawals),
            },
            'recent_transactions': [
                {
                    'id': str(t.id),
                    'type': t.entry_type,
                    'direction': t.direction,
                    'amount': float(t.amount),
                    'status': t.status,
                    'date': t.created_at.isoformat(),
                }
                for t in recent_ledger
            ]
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('contributions_basic')
def wallet_transactions(request):
    """
    GET /api/app/wallet/transactions/
    Get ledger transactions with filters (type, status, date range).
    """
    try:
        user = request.user
        
        # Get filter params
        entry_type = request.query_params.get('type')
        tx_status = request.query_params.get('status')
        date_from = request.query_params.get('from')
        date_to = request.query_params.get('to')
        search = request.query_params.get('search')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        
        # Base query
        transactions = LedgerEntry.objects.filter(
            wallet__owner_type='USER',
            wallet__owner_id=user.id
        ).order_by('-created_at')
        
        # Apply filters
        if entry_type:
            transactions = transactions.filter(entry_type=entry_type)
        
        if tx_status:
            transactions = transactions.filter(status=tx_status)
        
        if date_from:
            transactions = transactions.filter(created_at__gte=date_from)
        
        if date_to:
            transactions = transactions.filter(created_at__lte=date_to)
        
        if search:
            transactions = transactions.filter(
                Q(reference__icontains=search) |
                Q(provider_ref__icontains=search)
            )
        
        # Paginate
        start = (page - 1) * page_size
        end = start + page_size
        total = transactions.count()
        transactions_page = transactions[start:end]
        
        return Response({
            'transactions': [
                {
                    'id': str(t.id),
                    'reference': t.reference,
                    'type': t.entry_type,
                    'direction': t.direction,
                    'amount': float(t.amount),
                    'status': t.status,
                    'provider': t.provider,
                    'provider_ref': t.provider_ref,
                    'meta': t.meta,
                    'date': t.created_at.isoformat(),
                }
                for t in transactions_page
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'pages': (total + page_size - 1) // page_size
            }
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@require_feature('contributions_basic')
def wallet_validate(request):
    """
    GET /api/app/wallet/validate/
    Validate a wallet operation (deposit/withdrawal amount).
    """
    try:
        user = request.user
        operation = request.query_params.get('operation')
        amount = request.query_params.get('amount')
        
        if not operation or not amount:
            return Response(
                {'error': 'operation and amount are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            amount = float(amount)
        except ValueError:
            return Response(
                {'error': 'Invalid amount'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get wallet
        wallet = Wallet.objects.filter(
            owner_type='USER',
            owner_id=user.id
        ).first()
        
        if not wallet:
            return Response(
                {'error': 'Wallet not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        errors = []
        warnings = []
        valid = True
        
        if operation == 'deposit':
            if amount < MIN_DEPOSIT:
                errors.append(f'Minimum deposit is {MIN_DEPOSIT} KES')
                valid = False
            if amount > MAX_DEPOSIT:
                errors.append(f'Maximum deposit is {MAX_DEPOSIT} KES')
                valid = False
                
        elif operation == 'withdraw':
            today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_withdrawals = LedgerEntry.objects.filter(
                wallet__owner_type='USER',
                wallet__owner_id=user.id,
                entry_type='WITHDRAWAL',
                direction='debit',
                status='success',
                created_at__gte=today_start
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            daily_remaining = DAILY_WITHDRAWAL_LIMIT - float(today_withdrawals)
            
            if amount < MIN_WITHDRAWAL:
                errors.append(f'Minimum withdrawal is {MIN_WITHDRAWAL} KES')
                valid = False
            if amount > MAX_WITHDRAWAL:
                errors.append(f'Maximum withdrawal per transaction is {MAX_WITHDRAWAL} KES')
                valid = False
            if amount > daily_remaining:
                errors.append(f'Daily limit exceeded. Remaining: {daily_remaining} KES')
                valid = False
            if float(wallet.available_balance) < amount:
                errors.append('Insufficient balance')
                valid = False
                
            if amount > 10000:
                warnings.append('Large transaction: OTP confirmation required')
                
        else:
            return Response(
                {'error': 'Invalid operation. Use deposit or withdraw'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response({
            'valid': valid,
            'operation': operation,
            'amount': amount,
            'errors': errors,
            'warnings': warnings,
            'currency': CURRENCY,
        })
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ========================================================================
# PUBLIC SECURITY INFO ENDPOINT
# ========================================================================

@api_view(['GET'])
@permission_classes([AllowAny])
def public_security_info(request):
    """
    Public security information endpoint.
    Provides general security stats and features for the public Security page.
    No authentication required.
    """
    from apps.accounts.models import User
    from apps.chama.models import Chama
    from django.db.models import Count
    
    try:
        # Get platform statistics
        total_users = User.objects.filter(is_active=True).count()
        total_chamas = Chama.objects.filter(is_active=True).count()
        
        # Security features
        security_features = {
            'mfa_enabled': True,
            'encryption': '256-bit AES',
            'compliance': ['Kenya DPA', 'PCI-DSS Level 1'],
            'audit_logging': True,
            'real_time_monitoring': True,
            'fraud_detection': True,
        }
        
        # Security best practices
        best_practices = [
            {
                'title': 'Multi-Factor Authentication',
                'description': 'Every login requires OTP verification via SMS and email',
                'icon': 'shield',
            },
            {
                'title': 'Encrypted Transactions',
                'description': 'All financial data is encrypted with 256-bit AES encryption',
                'icon': 'lock',
            },
            {
                'title': 'Real-time Monitoring',
                'description': '24/7 fraud detection and suspicious activity alerts',
                'icon': 'eye',
            },
            {
                'title': 'Audit Logging',
                'description': 'Complete audit trail of all system activities',
                'icon': 'clipboard',
            },
            {
                'title': 'Role-Based Access',
                'description': 'Granular permissions ensure data access is properly controlled',
                'icon': 'users',
            },
            {
                'title': 'M-Pesa Integration',
                'description': 'Secure STK Push with real-time transaction reconciliation',
                'icon': 'phone',
            },
        ]
        
        # Compliance info
        compliance = {
            'name': 'Kenya Data Protection Act',
            'description': 'We are fully compliant with Kenya\'s data protection regulations',
            'badge': 'KDPA Compliant',
        }
        
        # Contact info for security issues
        security_contact = {
            'email': 'security@chama.co.ke',
            'response_time': '24 hours',
            'encrypted': True,
        }
        
        return Response({
            'platform_stats': {
                'total_users': total_users,
                'total_chamas': total_chamas,
            },
            'security_features': security_features,
            'best_practices': best_practices,
            'compliance': compliance,
            'security_contact': security_contact,
        })
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
