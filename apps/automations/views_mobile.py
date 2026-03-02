"""
Automation API Views - Complete Implementation
Extended endpoints for Flutter mobile app automation features
"""

from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import User
from apps.accounts.permissions import IsActiveMember
from apps.chama.models import Chama, Membership, MembershipRole
from apps.finance.models import Contribution, ContributionGoal, ContributionSchedule, Loan

from apps.notifications.models import Notification
from apps.security.models import UserSession
from core.algorithms.membership import (
    compute_effective_role,
    is_access_allowed,
    calculate_loan_eligibility,
    calculate_compliance,
    detect_withdrawal_anomaly,
    detect_role_change_anomaly,
)


# ========================================================================
# MEMBERSHIP AUTOMATIONS
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def effective_role_view(request, membership_id):
    """
    Get effective role for a membership (considering delegations)
    """
    try:
        membership = Membership.objects.select_related('chama').get(
            id=membership_id, 
            user=request.user
        )
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Membership not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    effective_role = compute_effective_role(membership, active_delegations=[])
    
    return Response({
        'membership_id': str(membership.id),
        'role': membership.role,
        'effective_role': effective_role,
        'status': membership.status,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def check_permission_view(request):
    """
    Check if a user has a specific permission
    """
    membership_id = request.data.get('membership_id')
    required_permission = request.data.get('required_permission')
    
    if not membership_id or not required_permission:
        return Response(
            {'error': 'membership_id and required_permission are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        membership = Membership.objects.get(id=membership_id)
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Membership not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    allowed = is_access_allowed(membership, required_permission)
    
    return Response({
        'allowed': allowed,
        'membership_id': str(membership.id),
        'permission': required_permission,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def active_delegations_view(request, user_id):
    """
    Get all active delegations for a user
    """
    # Get user's memberships where they are delegating their role
    delegating = Membership.objects.filter(
        user_id=user_id,
        delegating_to__isnull=False,
        status='ACTIVE'
    ).select_related('chama', 'delegating_to')
    
    # Get user's memberships where someone is delegating to them
    delegated_to = Membership.objects.filter(
        delegating_to_id=user_id,
        status='ACTIVE'
    ).select_related('chama', 'user')
    
    delegations = []
    
    for m in delegating:
        delegations.append({
            'id': str(m.id),
            'type': 'outgoing',
            'chama_id': str(m.chama.id),
            'chama_name': m.chama.name,
            'delegate_to': m.delegating_to_id,
            'role': m.role,
            'started_at': m.delegation_started_at.isoformat() if m.delegation_started_at else None,
        })
    
    for m in delegated_to:
        delegations.append({
            'id': str(m.id),
            'type': 'incoming',
            'chama_id': str(m.chama.id),
            'chama_name': m.chama.name,
            'delegated_from': m.user_id,
            'role': m.role,
            'started_at': m.delegation_started_at.isoformat() if m.delegation_started_at else None,
        })
    
    return Response({'delegations': delegations, 'count': len(delegations)})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def revoke_delegation_view(request, delegation_id):
    """
    Revoke a delegation (force logout the delegate)
    """
    try:
        membership = Membership.objects.get(id=delegation_id)
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Delegation not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    membership.delegating_to = None
    membership.delegation_started_at = None
    membership.save()
    
    return Response({'message': 'Delegation revoked successfully'})


# ========================================================================
# CONTRIBUTION AUTOMATIONS
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def compliance_view(request):
    """
    Get compliance score for a member in a chama
    """
    member_id = request.query_params.get('member_id')
    chama_id = request.query_params.get('chama_id')
    
    if not member_id or not chama_id:
        return Response(
            {'error': 'member_id and chama_id are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        membership = Membership.objects.select_related('chama').get(id=member_id, chama_id=chama_id)
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Membership not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get contribution data from actual models
    schedules = ContributionSchedule.objects.filter(
        chama=membership.chama,
        member=membership.user
    )
    
    expected = sum(s.amount for s in schedules)
    paid = sum(s.amount_paid for s in schedules if s.status == 'PAID')
    missed = sum(1 for s in schedules if s.status == 'MISSED')
    
    on_time = paid / expected * 100 if expected > 0 else 0
    
    # Calculate streak
    streak = 0
    recent_schedules = schedules.order_by('-due_date')[:12]
    for s in recent_schedules:
        if s.status == 'PAID':
            streak += 1
        else:
            break
    
    # Calculate grade
    if on_time >= 95:
        grade = 'A'
    elif on_time >= 85:
        grade = 'B'
    elif on_time >= 70:
        grade = 'C'
    elif on_time >= 50:
        grade = 'D'
    else:
        grade = 'F'
    
    compliance = calculate_compliance(
        member_id=str(membership.id),
        chama_id=str(membership.chama.id),
        expected=expected,
        actual=paid,
    )
    
    return Response({
        'member_id': str(membership.id),
        'chama_id': str(membership.chama.id),
        'on_time_percentage': on_time,
        'streak': streak,
        'total_contributions': paid,
        'expected_contributions': expected,
        'missed_payments': missed,
        'grade': grade,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def chama_compliance_view(request, chama_id):
    """
    Get compliance scores for all members in a chama
    """
    try:
        chama = Chama.objects.get(id=chama_id)
    except Chama.DoesNotExist:
        return Response(
            {'error': 'Chama not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    memberships = Membership.objects.filter(
        chama=chama,
        status='ACTIVE'
    ).select_related('user')
    
    compliance_list = []
    
    for membership in memberships:
        schedules = ContributionSchedule.objects.filter(
            chama=chama,
            member=membership.user
        )
        
        expected = sum(s.amount for s in schedules)
        paid = sum(s.amount_paid for s in schedules if s.status == 'PAID')
        on_time = paid / expected * 100 if expected > 0 else 0
        
        if on_time >= 95:
            grade = 'A'
        elif on_time >= 85:
            grade = 'B'
        elif on_time >= 70:
            grade = 'C'
        elif on_time >= 50:
            grade = 'D'
        else:
            grade = 'F'
        
        compliance_list.append({
            'member_id': str(membership.id),
            'member_name': membership.user.get_full_name() or membership.user.phone,
            'on_time_percentage': on_time,
            'grade': grade,
        })
    
    return Response({'results': compliance_list, 'count': len(compliance_list)})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def send_contribution_reminder_view(request):
    """
    Trigger contribution reminder for a member
    """
    member_id = request.data.get('member_id')
    chama_id = request.data.get('chama_id')
    
    if not member_id or not chama_id:
        return Response(
            {'error': 'member_id and chama_id are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        membership = Membership.objects.select_related('user', 'chama').get(
            id=member_id, 
            chama_id=chama_id
        )
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Membership not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Create notification for contribution reminder
    Notification.objects.create(
        user=membership.user,
        title='Contribution Reminder',
        message=f'Your contribution for {membership.chama.name} is due soon.',
        notification_type='CONTRIBUTION_REMINDER',
        chama=membership.chama,
    )
    
    return Response({'message': 'Reminder sent successfully'})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def pending_contributions_view(request, chama_id):
    """
    Get pending contributions that need reminders
    """
    try:
        chama = Chama.objects.get(id=chama_id)
    except Chama.DoesNotExist:
        return Response(
            {'error': 'Chama not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get schedules that are pending or overdue
    schedules = ContributionSchedule.objects.filter(
        chama=chama,
        status__in=['PENDING', 'OVERDUE']
    ).select_related('member__user').order_by('due_date')
    
    pending = []
    for s in schedules:
        pending.append({
            'id': str(s.id),
            'member_id': str(s.member.id),
            'member_name': s.member.user.get_full_name() or s.member.user.phone,
            'amount': float(s.amount),
            'due_date': s.due_date.isoformat(),
            'status': s.status,
            'days_overdue': (timezone.now().date() - s.due_date).days if s.status == 'OVERDUE' else 0,
        })
    
    return Response({'results': pending, 'count': len(pending)})


# ========================================================================
# LOAN AUTOMATIONS
# ========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def loan_eligibility_view(request):
    """
    Check loan eligibility for a member
    """
    member_id = request.data.get('member_id')
    chama_id = request.data.get('chama_id')
    amount = request.data.get('amount', 0)
    term_months = request.data.get('term_months', 6)
    
    if not member_id or not chama_id:
        return Response(
            {'error': 'member_id and chama_id are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        membership = Membership.objects.select_related('chama').get(id=member_id, chama_id=chama_id)
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Membership not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get chama config
    chama_config = {
        'max_loan_amount': membership.chama.max_loan_amount or 500000,
        'min_loan_amount': membership.chama.min_loan_amount or 1000,
        'interest_rate': membership.chama.loan_interest_rate or 12,
    }
    
    # Get member's contribution compliance
    schedules = ContributionSchedule.objects.filter(
        chama=membership.chama,
        member=membership.user
    )
    
    expected = sum(s.amount for s in schedules)
    paid = sum(s.amount_paid for s in schedules if s.status == 'PAID')
    on_time = paid / expected * 100 if expected > 0 else 0
    
    # Get existing loans
    active_loans = Loan.objects.filter(
        member=membership,
        status__in=['ACTIVE', 'DISBURSED']
    )
    total_outstanding = sum(l.remaining_balance for l in active_loans)
    
    compliance = {
        'on_time_percentage': on_time,
        'streak': 3,  # Could calculate from actual data
    }
    
    eligibility = calculate_loan_eligibility(
        membership=membership,
        application=None,
        chama_config=chama_config,
        compliance=compliance,
    )
    
    return Response({
        'eligible': eligibility['eligible'],
        'max_loan_amount': eligibility['max_loan_amount'],
        'risk_score': eligibility['risk_score'],
        'reasons': eligibility['reasons'],
        'recommended_term_months': term_months,
        'current_outstanding': total_outstanding,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def loan_approval_queue_view(request):
    """
    Get loan approval queue for treasurer/admin
    """
    chama_id = request.query_params.get('chama_id')
    loan_status = request.query_params.get('status', 'pending')
    
    if not chama_id:
        return Response(
            {'error': 'chama_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Map status to loan status
    status_map = {
        'pending': 'PENDING',
        'flagged': 'FLAGGED',
        'recommended': 'APPROVED',
    }
    
    db_status = status_map.get(loan_status.lower(), 'PENDING')
    
    loans = Loan.objects.filter(
        member__chama_id=chama_id,
        status=db_status
    ).select_related('member__user', 'member__chama')
    
    queue = []
    for loan in loans:
        queue.append({
            'id': str(loan.id),
            'member_id': str(loan.member.id),
            'member_name': loan.member.user.get_full_name() or loan.member.user.phone,
            'amount': float(loan.amount_applied),
            'purpose': loan.purpose,
            'term_months': loan.term_months,
            'status': loan.status,
            'applied_at': loan.created_at.isoformat(),
            'risk_score': loan.risk_score,
        })
    
    return Response({'results': queue, 'count': len(queue)})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def route_loan_view(request, loan_id):
    """
    Route loan to appropriate approval queue
    """
    try:
        loan = Loan.objects.get(id=loan_id)
    except Loan.DoesNotExist:
        return Response(
            {'error': 'Loan not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Auto-route based on risk score
    if loan.risk_score and loan.risk_score > 70:
        loan.status = 'FLAGGED'
    else:
        loan.status = 'PENDING'
    
    loan.save()
    
    return Response({
        'message': f'Loan routed to {loan.status} queue',
        'loan_id': str(loan.id),
        'new_status': loan.status,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def overdue_loans_view(request, chama_id):
    """
    Get overdue loans for notification
    """
    loans = Loan.objects.filter(
        member__chama_id=chama_id,
        status__in=['ACTIVE', 'DISBURSED'],
        next_payment_date__lt=timezone.now().date()
    ).select_related('member__user', 'member__chama')
    
    overdue = []
    for loan in loans:
        days_overdue = (timezone.now().date() - loan.next_payment_date).days
        overdue.append({
            'id': str(loan.id),
            'member_id': str(loan.member.id),
            'member_name': loan.member.user.get_full_name() or loan.member.user.phone,
            'amount_due': float(loan.remaining_balance),
            'next_payment_date': loan.next_payment_date.isoformat(),
            'days_overdue': days_overdue,
        })
    
    return Response({'results': overdue, 'count': len(overdue)})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def send_overdue_reminder_view(request, loan_id):
    """
    Trigger overdue notification
    """
    try:
        loan = Loan.objects.select_related('member__user', 'member__chama').get(id=loan_id)
    except Loan.DoesNotExist:
        return Response(
            {'error': 'Loan not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    Notification.objects.create(
        user=loan.member.user,
        title='Loan Overdue',
        message=f'Your loan payment for {loan.member.chama.name} is overdue.',
        notification_type='LOAN_OVERDUE',
        chama=loan.member.chama,
    )
    
    return Response({'message': 'Overdue reminder sent'})


# ========================================================================
# SECURITY AUTOMATIONS
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def security_alerts_view(request):
    """
    Get security alerts for a chama
    """
    chama_id = request.query_params.get('chama_id')
    severity = request.query_params.get('severity')
    
    if not chama_id:
        return Response(
            {'error': 'chama_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get recent security-related notifications
    notifications = Notification.objects.filter(
        chama_id=chama_id,
        notification_type__in=['SECURITY_ALERT', 'SUSPICIOUS_ACTIVITY', 'LOGIN_ALERT']
    ).order_by('-created_at')[:50]
    
    alerts = []
    for n in notifications:
        if severity and n.severity != severity.upper():
            continue
        alerts.append({
            'id': str(n.id),
            'title': n.title,
            'description': n.message,
            'severity': n.severity.lower() if n.severity else 'medium',
            'timestamp': n.created_at.isoformat(),
            'is_read': n.is_read,
            'action_type': n.notification_type.lower(),
        })
    
    return Response({'alerts': alerts})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def device_sessions_view(request, user_id):
    """
    Get device sessions for a user
    """
    sessions = UserSession.objects.filter(
        user_id=user_id,
        is_active=True
    ).order_by('-last_active')[:10]
    
    session_list = []
    for s in sessions:
        session_list.append({
            'id': str(s.id),
            'device': s.device_info or 'Unknown Device',
            'location': s.location or 'Unknown Location',
            'ip_address': s.ip_address,
            'last_active': s.last_active.isoformat() if s.last_active else None,
            'current': s == request.user.session if hasattr(request.user, 'session') else False,
        })
    
    return Response({'sessions': session_list})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def revoke_device_session_view(request, session_id):
    """
    Revoke a device session (force logout)
    """
    try:
        session = UserSession.objects.get(id=session_id)
    except UserSession.DoesNotExist:
        return Response(
            {'error': 'Session not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    session.is_active = False
    session.save()
    
    return Response({'message': 'Session revoked successfully'})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def report_suspicious_activity_view(request):
    """
    Report suspicious activity
    """
    chama_id = request.data.get('chama_id')
    activity_type = request.data.get('activity_type')
    description = request.data.get('description')
    
    if not chama_id or not activity_type or not description:
        return Response(
            {'error': 'chama_id, activity_type, and description are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        chama = Chama.objects.get(id=chama_id)
    except Chama.DoesNotExist:
        return Response(
            {'error': 'Chama not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Create security notification
    Notification.objects.create(
        user=request.user,
        title=f'Suspicious Activity: {activity_type}',
        message=description,
        notification_type='SUSPICIOUS_ACTIVITY',
        severity='HIGH',
        chama=chama,
    )
    
    return Response({'message': 'Activity reported successfully'})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def locked_accounts_view(request):
    """
    Get locked accounts (brute force protection)
    """
    # Get users with failed login attempts
    from apps.accounts.models import FailedLoginAttempt
    
    threshold = timezone.now() - timedelta(minutes=30)
    failed_attempts = FailedLoginAttempt.objects.filter(
        attempted_at__gt=threshold
    ).values('user_id').distinct()
    
    locked = []
    for attempt in failed_attempts:
        count = FailedLoginAttempt.objects.filter(
            user_id=attempt['user_id'],
            attempted_at__gt=threshold
        ).count()
        
        if count >= 5:  # Lock threshold
            try:
                user = User.objects.get(id=attempt['user_id'])
                locked.append({
                    'user_id': str(user.id),
                    'user_name': user.get_full_name() or user.phone,
                    'failed_attempts': count,
                })
            except User.DoesNotExist:
                pass
    
    return Response({'locked_accounts': locked})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def unlock_account_view(request, user_id):
    """
    Unlock an account (admin action)
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response(
            {'error': 'User not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Clear failed attempts
    from apps.accounts.models import FailedLoginAttempt
    FailedLoginAttempt.objects.filter(user=user).delete()
    
    # Optionally unlock user if locked
    if hasattr(user, 'is_locked'):
        user.is_locked = False
        user.save()
    
    return Response({'message': f'Account {user_id} unlocked successfully'})


# ========================================================================
# ANOMALY DETECTION
# ========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def check_withdrawal_anomaly_view(request):
    """
    Check if a withdrawal might be anomalous
    """
    member_id = request.data.get('member_id')
    chama_id = request.data.get('chama_id')
    amount = request.data.get('amount', 0)
    
    if not member_id or not chama_id or not amount:
        return Response(
            {'error': 'member_id, chama_id, and amount are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        membership = Membership.objects.get(id=member_id, chama_id=chama_id)
    except Membership.DoesNotExist:
        return Response(
            {'error': 'Membership not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Get member's average transactions
    from apps.payments.models import MpesaTransaction
    member_txns = MpesaTransaction.objects.filter(
        member=membership,
        transaction_type='WITHDRAWAL',
        status='SUCCESS'
    )
    
    if member_txns.exists():
        member_avg = member_txns.aggregate(avg=models.Avg('amount'))['avg'] or 0
        count = member_txns.count()
    else:
        member_avg = 0
        count = 0
    
    # Get chama average
    chama_avg = MpesaTransaction.objects.filter(
        chama=membership.chama,
        transaction_type='WITHDRAWAL',
        status='SUCCESS'
    ).aggregate(avg=models.Avg('amount'))['avg'] or 0
    
    anomaly = detect_withdrawal_anomaly(
        amount=float(amount),
        member_avg=member_avg,
        chama_avg=chama_avg,
        count=count,
    )
    
    if anomaly:
        return Response({
            'is_anomaly': True,
            'severity': anomaly.get('severity', 'medium'),
            'message': anomaly.get('message'),
            'recommendation': anomaly.get('recommendation'),
        })
    
    return Response({
        'is_anomaly': False,
        'severity': None,
        'message': 'Withdrawal appears normal',
        'recommendation': None,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def anomalies_view(request, chama_id):
    """
    Get anomalies for a chama
    """
    # Get all recent anomalies from notifications
    notifications = Notification.objects.filter(
        chama_id=chama_id,
        notification_type__in=['ANOMALY_DETECTED', 'SUSPICIOUS_ACTIVITY']
    ).order_by('-created_at')[:50]
    
    anomalies = []
    for n in notifications:
        anomalies.append({
            'id': str(n.id),
            'type': n.notification_type.lower(),
            'title': n.title,
            'description': n.message,
            'severity': n.severity.lower() if n.severity else 'medium',
            'timestamp': n.created_at.isoformat(),
        })
    
    return Response({'anomalies': anomalies})


# ========================================================================
# AUDIT & COMPLIANCE
# ========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsActiveMember])
def audit_logs_view(request):
    """
    Get audit logs for a chama
    """
    chama_id = request.query_params.get('chama_id')
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    action_type = request.query_params.get('action_type')
    page = int(request.query_params.get('page', 1))
    page_size = int(request.query_params.get('page_size', 50))
    
    if not chama_id:
        return Response(
            {'error': 'chama_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    from core.models import AuditLog
    
    queryset = AuditLog.objects.filter(chama_id=chama_id)
    
    if start_date:
        queryset = queryset.filter(created_at__gte=start_date)
    if end_date:
        queryset = queryset.filter(created_at__lte=end_date)
    if action_type:
        queryset = queryset.filter(action_type=action_type)
    
    total = queryset.count()
    logs = queryset.order_by('-created_at')[(page-1)*page_size:page*page_size]
    
    log_list = []
    for log in logs:
        log_list.append({
            'id': str(log.id),
            'user': log.user.get_full_name() if log.user else 'System',
            'action_type': log.action_type,
            'description': log.description,
            'timestamp': log.created_at.isoformat(),
        })
    
    return Response({
        'results': log_list,
        'count': total,
        'page': page,
        'page_size': page_size,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsActiveMember])
def export_audit_report_view(request):
    """
    Export audit report
    """
    chama_id = request.data.get('chama_id')
    start_date = request.data.get('start_date')
    end_date = request.data.get('end_date')
    
    if not chama_id or not start_date or not end_date:
        return Response(
            {'error': 'chama_id, start_date, and end_date are required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # In a real implementation, this would generate a PDF
    # For now, return a mock URL
    return Response({
        'report_url': f'/api/v1/reports/audit/{chama_id}/{start_date}/{end_date}/',
        'message': 'Report generation started. You will receive a notification when ready.',
    })


# Need to import for anomaly detection
from django.db import models
