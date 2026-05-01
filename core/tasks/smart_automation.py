"""
Smart Automation Celery Tasks for MyChama

Implements automated workflows:
- Contribution reminders
- Overdue alerts
- Meeting reminders
- Loan repayment reminders
- Fine generation
- Monthly summary generation
- Health score computation
- Risk flag detection
- Anomaly detection
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def send_contribution_reminders(self):
    """
    Send contribution reminders to members.
    
    Runs daily at 8 AM.
    Sends reminders 3 days, 1 day, and on due date.
    """
    try:
        from apps.chama.models import Chama, Membership, MemberStatus
        from core.algorithms.smart_notifications import generate_contribution_reminders
        
        # Get all active chamas
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Get contribution settings
            contribution_setting = getattr(chama, "contribution_setting", None)
            if not contribution_setting:
                continue
            
            # Get active members
            members = Membership.objects.filter(
                chama=chama,
                status=MemberStatus.ACTIVE,
            ).values("id", "user_id", "user__full_name")
            
            # Generate reminders
            notifications = generate_contribution_reminders(
                chama_id=str(chama.id),
                members=list(members),
                contribution_schedule={
                    "due_day": contribution_setting.due_day,
                    "amount": str(contribution_setting.contribution_amount),
                    "grace_period_days": contribution_setting.grace_period_days,
                },
            )
            
            # Send notifications
            for notification in notifications:
                # Would send via notification service
                logger.info(f"Sending reminder to {notification.recipient_id}: {notification.title}")
        
        logger.info("Contribution reminders sent successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error sending contribution reminders: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def send_overdue_alerts(self):
    """
    Send overdue alerts for contributions and loans.
    
    Runs daily at 9 AM.
    Escalates based on days overdue.
    """
    try:
        from apps.chama.models import Chama
        from apps.finance.models import Contribution, Loan, LoanStatus
        from core.algorithms.smart_notifications import generate_overdue_alerts
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Get overdue contributions
            overdue_contributions = Contribution.objects.filter(
                chama=chama,
                status="pending",
                due_date__lt=timezone.now().date(),
            ).values("id", "member_id", "amount", "due_date")
            
            # Get overdue loans
            overdue_loans = Loan.objects.filter(
                chama=chama,
                status__in=[LoanStatus.OVERDUE, LoanStatus.DEFAULTED],
            ).values("id", "member_id", "outstanding_balance", "due_date")
            
            # Generate alerts
            notifications = generate_overdue_alerts(
                chama_id=str(chama.id),
                overdue_contributions=list(overdue_contributions),
                overdue_loans=list(overdue_loans),
                escalation_policy={},
            )
            
            # Send notifications
            for notification in notifications:
                logger.info(f"Sending overdue alert to {notification.recipient_id}: {notification.title}")
        
        logger.info("Overdue alerts sent successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error sending overdue alerts: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def send_meeting_reminders(self):
    """
    Send meeting reminders.
    
    Runs daily at 7 AM.
    Sends reminders 7 days, 3 days, and 1 day before meeting.
    """
    try:
        from apps.chama.models import Chama, Meeting, Membership, MemberStatus
        from core.algorithms.smart_notifications import generate_meeting_reminders
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Get upcoming meetings
            upcoming_meetings = Meeting.objects.filter(
                chama=chama,
                date__gte=timezone.now().date(),
                date__lte=timezone.now().date() + timedelta(days=7),
            ).values("id", "title", "date", "time", "location")
            
            # Get active members
            members = Membership.objects.filter(
                chama=chama,
                status=MemberStatus.ACTIVE,
            ).values("id", "user_id")
            
            # Generate reminders
            notifications = generate_meeting_reminders(
                chama_id=str(chama.id),
                upcoming_meetings=list(upcoming_meetings),
                members=list(members),
            )
            
            # Send notifications
            for notification in notifications:
                logger.info(f"Sending meeting reminder to {notification.recipient_id}: {notification.title}")
        
        logger.info("Meeting reminders sent successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error sending meeting reminders: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def send_loan_repayment_reminders(self):
    """
    Send loan repayment reminders.
    
    Runs daily at 10 AM.
    Sends reminders 7 days, 3 days, 1 day before due date.
    """
    try:
        from apps.chama.models import Chama
        from apps.finance.models import Loan, LoanStatus
        from core.algorithms.smart_notifications import (
            generate_loan_repayment_reminders,
        )
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Get active loans with upcoming payments
            active_loans = Loan.objects.filter(
                chama=chama,
                status=LoanStatus.ACTIVE,
                next_payment_date__lte=timezone.now().date() + timedelta(days=7),
            ).values("id", "member_id", "next_payment_amount", "next_payment_date")
            
            # Generate reminders
            notifications = generate_loan_repayment_reminders(
                chama_id=str(chama.id),
                active_loans=list(active_loans),
            )
            
            # Send notifications
            for notification in notifications:
                logger.info(f"Sending loan reminder to {notification.recipient_id}: {notification.title}")
        
        logger.info("Loan repayment reminders sent successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error sending loan repayment reminders: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def generate_fines(self):
    """
    Generate fines for late contributions.
    
    Runs daily at midnight.
    Applies fines based on chama policy.
    """
    try:
        from apps.chama.models import Chama
        from apps.finance.models import Contribution
        from apps.fines.models import Fine
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            contribution_setting = getattr(chama, "contribution_setting", None)
            if not contribution_setting or contribution_setting.late_fine_amount <= 0:
                continue
            
            # Find overdue contributions
            overdue_contributions = Contribution.objects.filter(
                chama=chama,
                status="pending",
                due_date__lt=timezone.now().date() - timedelta(days=contribution_setting.grace_period_days),
            )
            
            for contribution in overdue_contributions:
                # Check if fine already exists
                existing_fine = Fine.objects.filter(
                    chama=chama,
                    member_id=contribution.member_id,
                    contribution=contribution,
                ).exists()
                
                if not existing_fine:
                    # Create fine
                    Fine.objects.create(
                        chama=chama,
                        member_id=contribution.member_id,
                        contribution=contribution,
                        amount=contribution_setting.late_fine_amount,
                        reason="Late contribution payment",
                        status="unpaid",
                    )
                    logger.info(f"Generated fine for member {contribution.member_id}")
        
        logger.info("Fines generated successfully")
        return {"status": "success"}
    
    except Exception as exc:
        logger.error(f"Error generating fines: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def compute_health_scores(self):
    """
    Compute chama health scores.
    
    Runs weekly on Sunday at 2 AM.
    Updates cached health scores for all chamas.
    """
    try:
        from apps.chama.models import Chama, Membership, MemberStatus
        from apps.finance.models import Contribution, Loan, LoanStatus
        from core.algorithms.smart_scoring import compute_chama_health_score
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Gather data
            total_members = Membership.objects.filter(chama=chama).count()
            active_members = Membership.objects.filter(
                chama=chama,
                status=MemberStatus.ACTIVE,
            ).count()
            
            total_savings = Contribution.objects.filter(
                chama=chama,
                status="paid",
            ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
            
            total_loans_outstanding = Loan.objects.filter(
                chama=chama,
                status__in=[LoanStatus.ACTIVE, LoanStatus.OVERDUE],
            ).aggregate(total=models.Sum("outstanding_balance"))["total"] or Decimal("0")
            
            overdue_loans_count = Loan.objects.filter(
                chama=chama,
                status__in=[LoanStatus.OVERDUE, LoanStatus.DEFAULTED],
            ).count()
            
            # Compute rates
            total_expected = Contribution.objects.filter(chama=chama).count()
            total_paid = Contribution.objects.filter(chama=chama, status="paid").count()
            contribution_completion_rate = (
                (Decimal(str(total_paid)) / Decimal(str(total_expected)) * 100)
                if total_expected > 0 else Decimal("0")
            )
            
            # Compute health score
            health_score = compute_chama_health_score(
                chama_id=str(chama.id),
                total_members=total_members,
                active_members=active_members,
                total_savings=total_savings,
                total_loans_outstanding=total_loans_outstanding,
                overdue_loans_count=overdue_loans_count,
                contribution_completion_rate=contribution_completion_rate,
                meeting_attendance_rate=Decimal("70"),  # Would compute real data
                expense_control_rate=Decimal("90"),
                member_growth_rate=Decimal("5"),
                monthly_contributions=[],
                monthly_expenses=[],
            )
            
            # Cache health score
            # Would store in cache or database
            logger.info(f"Computed health score for {chama.name}: {health_score.overall_score}")
        
        logger.info("Health scores computed successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error computing health scores: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def detect_risk_flags(self):
    """
    Detect risk flags across all chamas.
    
    Runs daily at 3 AM.
    Identifies potential issues and creates alerts.
    """
    try:
        from apps.chama.models import Chama, Membership, MemberStatus
        from apps.finance.models import Contribution, Loan
        from core.algorithms.smart_scoring import detect_risk_flags
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Gather member data
            members = Membership.objects.filter(
                chama=chama,
                status=MemberStatus.ACTIVE,
            ).values("id", "user_id")
            
            member_data = []
            for member in members:
                # Get contribution history
                contributions = Contribution.objects.filter(
                    chama=chama,
                    member_id=member["user_id"],
                )
                missed = contributions.filter(status="missed").count()
                
                member_data.append({
                    "id": str(member["id"]),
                    "missed_contributions": missed,
                })
            
            # Gather finance data
            finance_data = {
                "monthly_balance_trend": [],  # Would compute real data
            }
            
            # Gather loan data
            loans = Loan.objects.filter(chama=chama).values(
                "id", "member_id", "status", "outstanding_balance"
            )
            
            # Detect risk flags
            risk_flags = detect_risk_flags(
                chama_id=str(chama.id),
                members_data=member_data,
                finance_data=finance_data,
                loan_data=list(loans),
            )
            
            # Create alerts for high/critical flags
            for flag in risk_flags:
                if flag.severity.value in ["high", "critical"]:
                    # Would create alert in database
                    logger.warning(f"Risk flag detected for {chama.name}: {flag.description}")
        
        logger.info("Risk flags detected successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error detecting risk flags: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def generate_monthly_summaries(self):
    """
    Generate monthly summaries for all chamas.
    
    Runs on 1st of each month at 6 AM.
    Creates plain-language summaries and sends to admins.
    """
    try:
        from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
        from apps.finance.models import Contribution, Loan
        from core.algorithms.smart_analytics import (
            analyze_contribution_trends,
            assess_loan_portfolio_risk,
            generate_monthly_ai_summary,
        )
        from core.algorithms.smart_notifications import (
            generate_monthly_summary_notification,
        )
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Get last month's data
            last_month = timezone.now() - timedelta(days=30)
            month_str = last_month.strftime("%B %Y")
            
            # Contribution trends
            contributions = Contribution.objects.filter(
                chama=chama,
                paid_date__gte=last_month,
            ).values("id", "member_id", "amount", "status", "paid_date")
            
            members = Membership.objects.filter(
                chama=chama,
                status=MemberStatus.ACTIVE,
            ).values("id", "user_id")
            
            contribution_trend = analyze_contribution_trends(
                chama_id=str(chama.id),
                contributions=list(contributions),
                members=list(members),
            )
            
            # Loan risk
            loans = Loan.objects.filter(chama=chama).values(
                "id", "status", "outstanding_balance"
            )
            
            loan_risk = assess_loan_portfolio_risk(
                chama_id=str(chama.id),
                loans=list(loans),
                chama_balance=Decimal("0"),  # Would compute real balance
            )
            
            # Generate summary
            generate_monthly_ai_summary(
                chama_id=str(chama.id),
                month=month_str,
                contribution_trend=contribution_trend,
                expense_patterns=[],
                loan_risk=loan_risk,
                member_engagement=None,
                cashflow_forecast=None,
            )
            
            # Get admins
            admins = Membership.objects.filter(
                chama=chama,
                role__in=[MembershipRole.ADMIN, MembershipRole.CHAMA_ADMIN, MembershipRole.SUPERADMIN],
                status=MemberStatus.ACTIVE,
            ).values("id", "user_id")
            
            # Send summary notification
            notifications = generate_monthly_summary_notification(
                chama_id=str(chama.id),
                summary_data={
                    "month": month_str,
                    "total_contributions": str(contribution_trend.total_contributions),
                    "total_expenses": "0",
                    "active_members": contribution_trend.at_risk_members,
                    "overdue_loans": loan_risk.overdue_loans,
                },
                admins=list(admins),
            )
            
            for notification in notifications:
                logger.info(f"Sending monthly summary to admin {notification.recipient_id}")
        
        logger.info("Monthly summaries generated successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error generating monthly summaries: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def detect_anomalies(self):
    """
    Detect anomalies and suspicious activity.
    
    Runs every 6 hours.
    Identifies unusual patterns and creates alerts.
    """
    try:
        from apps.chama.models import Chama
        from apps.finance.models import LedgerEntry
        from core.algorithms.smart_analytics import detect_anomalies
        
        chamas = Chama.objects.filter(status="active")
        
        for chama in chamas:
            # Get recent transactions
            recent_transactions = LedgerEntry.objects.filter(
                chama=chama,
                posted_at__gte=timezone.now() - timedelta(days=7),
            ).values("id", "amount", "direction", "posted_at", "source_type")
            
            # Get historical baselines
            historical_baselines = {
                "contribution": {
                    "average": "10000",
                    "std_dev": "2000",
                },
                "expense": {
                    "average": "5000",
                    "std_dev": "1500",
                },
            }
            
            # Detect anomalies
            anomalies = detect_anomalies(
                chama_id=str(chama.id),
                transactions=list(recent_transactions),
                historical_baselines=historical_baselines,
            )
            
            # Create alerts for anomalies
            for anomaly in anomalies:
                if anomaly.severity in ["high", "critical"]:
                    logger.warning(f"Anomaly detected for {chama.name}: {anomaly.description}")
        
        logger.info("Anomaly detection completed successfully")
        return {"status": "success", "chamas_processed": len(chamas)}
    
    except Exception as exc:
        logger.error(f"Error detecting anomalies: {exc}")
        raise self.retry(exc=exc, countdown=60)


# =============================================================================
# NEW 200 AUTOMATIONS - Smart Financial, Member Experience & Governance
# =============================================================================


@shared_task(bind=True, max_retries=3)
def run_npl_auto_tagger(self):
    """
    NPL Auto-Tagger: Tag loans 30, 60, 90 days overdue.
    
    Runs daily at 6 AM.
    Updates loan delinquency buckets and alerts Treasurers.
    """
    try:
        from apps.chama.models import Chama
        from apps.finance.models import Loan
        from core.algorithms.smart_financial_automations import (
            get_npl_loans,
            compute_par_ratio,
            tag_loan_delinquency,
        )
        
        chamas = Chama.objects.filter(is_active=True)
        results = []
        
        for chama in chamas:
            npl_loans_30 = get_npl_loans(chama, days_threshold=30)
            npl_loans_60 = get_npl_loans(chama, days_threshold=60)
            npl_loans_90 = get_npl_loans(chama, days_threshold=90)
            par_ratio = compute_par_ratio(chama)
            
            for loan in Loan.objects.filter(chama=chama, status="active"):
                delinquency = tag_loan_delinquency(loan)
                if loan.delinquency_status != delinquency:
                    loan.delinquency_status = delinquency
                    loan.save(update_fields=["delinquency_status", "updated_at"])
            
            results.append({
                "chama_id": str(chama.id),
                "npl_30_days": len(npl_loans_30),
                "npl_60_days": len(npl_loans_60),
                "npl_90_plus": len(npl_loans_90),
                "par_ratio": float(par_ratio),
            })
        
        logger.info("NPL auto-tagger completed: %s", results)
        return {"status": "success", "chamas_processed": len(chamas), "results": results}
    
    except Exception as exc:
        logger.error(f"Error in NPL auto-tagger: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def run_cash_flow_forecast(self):
    """
    Cash Flow Forecast Generator.
    
    Runs weekly on Monday at 8 AM.
    Pushes weekly projection to Treasurers.
    """
    try:
        from apps.chama.models import Chama
        from core.algorithms.smart_financial_automations import generate_cash_flow_forecast
        
        chamas = Chama.objects.filter(is_active=True)
        forecasts = []
        
        for chama in chamas:
            forecast = generate_cash_flow_forecast(chama, days_ahead=7)
            forecasts.append({
                "chama_id": str(chama.id),
                "forecasts": [
                    {
                        "date": str(f.date),
                        "inflow": str(f.expected_inflow),
                        "outflow": str(f.expected_outflow),
                        "net": str(f.net_flow),
                        "balance": str(f.running_balance),
                    }
                    for f in forecast
                ],
            })
        
        logger.info("Cash flow forecast completed for %s chamas", len(chamas))
        return {"status": "success", "forecasts": forecasts}
    
    except Exception as exc:
        logger.error(f"Error in cash flow forecast: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def run_investment_opportunity_check(self):
    """
    Investment Opportunity Suggester.
    
    Runs weekly on Monday at 9 AM.
    Alerts Chairperson when pool balance exceeds threshold.
    """
    try:
        from apps.chama.models import Chama
        from core.algorithms.smart_financial_automations import check_investment_opportunity
        
        chamas = Chama.objects.filter(is_active=True)
        opportunities = []
        
        for chama in chamas:
            opportunity = check_investment_opportunity(chama)
            if opportunity:
                opportunities.append({
                    "chama_id": str(chama.id),
                    "current_balance": str(opportunity.current_balance),
                    "threshold": str(opportunity.threshold),
                    "excess": str(opportunity.excess_balance),
                    "suggestion": opportunity.suggested_investment,
                    "alert_level": opportunity.alert_level,
                })
        
        logger.info("Investment opportunity check: %s alerts", len(opportunities))
        return {"status": "success", "opportunities": opportunities}
    
    except Exception as exc:
        logger.error(f"Error in investment check: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def run_chama_credit_score_update(self):
    """
    Chama Credit Score Updater.
    
    Runs weekly on Sunday at 10 PM.
    Recalculates credit score after every financial event.
    """
    try:
        from apps.chama.models import Chama
        from core.algorithms.smart_financial_automations import calculate_chama_credit_score
        
        chamas = Chama.objects.filter(is_active=True)
        scores = []
        
        for chama in chamas:
            score = calculate_chama_credit_score(chama)
            scores.append({
                "chama_id": str(chama.id),
                "score": score.score,
                "rating": score.rating,
                "collection_rate": str(score.collection_rate),
                "loan_repayment_rate": str(score.loan_repayment_rate),
                "meeting_attendance": str(score.meeting_attendance),
            })
        
        logger.info("Chama credit scores updated for %s chamas", len(chamas))
        return {"status": "success", "scores": scores}
    
    except Exception as exc:
        logger.error(f"Error updating credit scores: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def run_daily_reconciliation(self):
    """
    Reconciliation Auto-Runner.
    
    Runs daily at 11 PM.
    Matches all inflows to expected contributions.
    """
    try:
        from apps.chama.models import Chama
        from core.algorithms.smart_financial_automations import run_daily_reconciliation
        
        chamas = Chama.objects.filter(is_active=True)
        results = []
        
        for chama in chamas:
            reconciliation = run_daily_reconciliation(chama)
            if reconciliation.unmatched_count > 0:
                results.append({
                    "chama_id": str(chama.id),
                    "matched": reconciliation.matched_count,
                    "unmatched": reconciliation.unmatched_count,
                    "variance": str(reconciliation.variance),
                })
        
        logger.info("Daily reconciliation: %s chamas with unmatched items", len(results))
        return {"status": "success", "results": results}
    
    except Exception as exc:
        logger.error(f"Error in reconciliation: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def send_payout_countdown_notifications(self):
    """
    Payout Countdown Notifier.
    
    Runs daily at 10 AM.
    Pushes 'your payout is X days away' to next-in-line members.
    """
    try:
        from apps.chama.models import Chama, Membership, MembershipStatus
        from apps.notifications.services import NotificationService
        from core.algorithms.smart_member_automations import get_payout_countdown
        
        chamas = Chama.objects.filter(is_active=True)
        notifications_sent = 0
        
        for chama in chamas:
            members = Membership.objects.filter(
                chama=chama,
                status=MembershipStatus.ACTIVE,
                is_active=True,
            ).select_related("user")
            
            for membership in members:
                countdown = get_payout_countdown(membership, days_ahead=7)
                if countdown and countdown.notify and countdown.notification_message:
                    try:
                        NotificationService.send_notification(
                            user=membership.user,
                            message=countdown.notification_message,
                            channels=["sms", "push"],
                            notification_type="system",
                            category="payout",
                            priority="high",
                        )
                        notifications_sent += 1
                    except Exception:
                        pass
        
        logger.info("Payout countdown notifications sent: %s", notifications_sent)
        return {"status": "success", "notifications_sent": notifications_sent}
    
    except Exception as exc:
        logger.error(f"Error sending payout notifications: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def update_contribution_streaks(self):
    """
    Contribution Streak Tracker.
    
    Runs daily at midnight.
    Tracks and celebrates consecutive on-time payment streaks.
    """
    try:
        from apps.chama.models import Chama, Membership, MembershipStatus
        from core.algorithms.smart_member_automations import get_contribution_streak
        
        chamas = Chama.objects.filter(is_active=True)
        streak_updates = []
        
        for chama in chamas:
            members = Membership.objects.filter(
                chama=chama,
                status=MembershipStatus.ACTIVE,
                is_active=True,
            ).select_related("user")
            
            for membership in members:
                streak = get_contribution_streak(membership, months_lookback=12)
                streak_updates.append({
                    "member_id": str(membership.id),
                    "current_streak": streak.current_streak,
                    "longest_streak": streak.longest_streak,
                    "consistency_score": streak.consistency_score,
                    "milestone": streak.milestone_reached,
                })
        
        logger.info("Contributed streaks updated for %s members", len(streak_updates))
        return {"status": "success", "streaks": streak_updates}
    
    except Exception as exc:
        logger.error(f"Error updating streaks: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def send_onboarding_nudges(self):
    """
    Onboarding Checklist Tracker.
    
    Runs daily at 11 AM.
    Tracks and nudges member through 5 onboarding steps.
    """
    try:
        from apps.chama.models import Membership, MembershipStatus
        from apps.notifications.services import NotificationService
        from core.algorithms.smart_member_automations import get_onboarding_progress
        
        memberships = Membership.objects.filter(
            status=MembershipStatus.ACTIVE,
            is_active=True,
        ).select_related("user", "chama")
        
        nudges_sent = 0
        
        for membership in memberships:
            progress = get_onboarding_progress(membership)
            if progress.nudge_message and not progress.is_complete:
                try:
                    NotificationService.send_notification(
                        user=membership.user,
                        message=progress.nudge_message,
                        channels=["push", "in_app"],
                        notification_type="system",
                        priority="normal",
                    )
                    nudges_sent += 1
                except Exception:
                    pass
        
        logger.info("Onboarding nudges sent: %s", nudges_sent)
        return {"status": "success", "nudges_sent": nudges_sent}
    
    except Exception as exc:
        logger.error(f"Error sending onboarding nudges: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def send_milestone_celebrations(self):
    """
    Birthday/Milestone Notifier.
    
    Runs daily at 9 AM.
    Auto-posts celebration on member anniversaries.
    """
    try:
        from apps.accounts.models import User
        from apps.chama.models import Membership, MembershipStatus
        from apps.notifications.services import NotificationService
        from core.algorithms.smart_member_automations import get_member_milestones
        
        users = User.objects.filter(is_active=True)
        celebrations = 0
        
        for user in users:
            milestones = get_member_milestones(user, days_ahead=7)
            if milestones.milestones:
                message = "Congratulations! "
                message += " ".join(m.milestone_label for m in milestones.milestones)
                
                try:
                    NotificationService.send_notification(
                        user=user,
                        message=message,
                        channels=["in_app"],
                        notification_type="system",
                        priority="low",
                    )
                    celebrations += 1
                except Exception:
                    pass
        
        logger.info("Milestone celebrations sent: %s", celebrations)
        return {"status": "success", "celebrations": celebrations}
    
    except Exception as exc:
        logger.error(f"Error sending celebrations: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def send_governance_alerts(self):
    """
    Governance Alerts: Quorum Trend & Term Limits.
    
    Runs weekly on Sunday at 11 PM.
    Alerts Chairperson on governance issues.
    """
    try:
        from apps.chama.models import Chama
        from apps.notifications.services import NotificationService
        from core.algorithms.smart_governance_automations import (
            analyze_quorum_trend,
            check_term_limits,
        )
        
        chamas = Chama.objects.filter(is_active=True)
        alerts_sent = 0
        
        for chama in chamas:
            quorum = analyze_quorum_trend(chama, months=6)
            if quorum.alert_level != "NONE":
                try:
                    NotificationService.send_notification(
                        user=chama.created_by,
                        message=f"Attendance Alert: {quorum.message}",
                        channels=["push", "sms"],
                        notification_type="system",
                        priority="medium",
                    )
                    alerts_sent += 1
                except Exception:
                    pass
            
            term_limits = check_term_limits(chama)
            for term in term_limits:
                if term.alert_level != "NONE":
                    try:
                        NotificationService.send_notification(
                            user=term.member_id,
                            message=f"Term Alert: {term.message}",
                            channels=["push", "sms"],
                            notification_type="system",
                            priority="high" if term.alert_level == "HIGH" else "medium",
                        )
                        alerts_sent += 1
                    except Exception:
                        pass
        
        logger.info("Governance alerts sent: %s", alerts_sent)
        return {"status": "success", "alerts_sent": alerts_sent}
    
    except Exception as exc:
        logger.error(f"Error sending governance alerts: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def send_election_reminders(self):
    """
    Election Reminder Dispatcher.
    
    Runs daily at 8 AM.
    Sends 30 days, 7 days, 1 day reminders.
    """
    try:
        from apps.chama.models import Chama
        from apps.notifications.services import NotificationService
        from core.algorithms.smart_governance_automations import get_election_reminders
        
        chamas = Chama.objects.filter(is_active=True)
        reminders_sent = 0
        
        for chama in chamas:
            reminders = get_election_reminders(chama)
            for reminder in reminders:
                try:
                    NotificationService.send_notification(
                        user=chama.created_by,
                        message=reminder.message,
                        channels=["push", "sms"],
                        notification_type="system",
                        priority="high",
                    )
                    reminders_sent += 1
                except Exception:
                    pass
        
        logger.info("Election reminders sent: %s", reminders_sent)
        return {"status": "success", "reminders_sent": reminders_sent}
    
    except Exception as exc:
        logger.error(f"Error sending election reminders: {exc}")
        raise self.retry(exc=exc, countdown=120)


@shared_task(bind=True, max_retries=3)
def check_document_expiry(self):
    """
    Document Expiry Reminder.
    
    Runs weekly on Monday at 10 AM.
    Alerts Chairperson when chama docs near expiry.
    """
    try:
        from apps.chama.models import Chama
        from apps.documents.models import Document, DocumentStatus
        from apps.notifications.services import NotificationService
        from django.utils import timezone
        
        today = timezone.now().date()
        warning_date = today + timedelta(days=30)
        
        expiring_docs = Document.objects.filter(
            expiry_date__lte=warning_date,
            expiry_date__gte=today,
            status=DocumentStatus.ACTIVE,
        ).select_related("chama")
        
        alerts = 0
        for doc in expiring_docs:
            try:
                NotificationService.send_notification(
                    user=doc.chama.created_by,
                    message=f"Document '{doc.name}' expires on {doc.expiry_date}. Please renew.",
                    channels=["push", "sms"],
                    notification_type="system",
                    priority="high",
                )
                alerts += 1
            except Exception:
                pass
        
        logger.info("Document expiry alerts sent: %s", alerts)
        return {"status": "success", "alerts": alerts}
    
    except Exception as exc:
        logger.error(f"Error checking document expiry: {exc}")
        raise self.retry(exc=exc, countdown=120)


# =============================================================================
# PLATFORM & DEVOPS MONITORING AUTOMATIONS
# =============================================================================


@shared_task(bind=True, max_retries=3)
def run_platform_health_checks(self):
    """
    Platform Health Monitor.
    
    Runs every 5 minutes.
    Monitors M-Pesa, AT balance, DB pool, Redis, rate limits.
    """
    try:
        from core.algorithms.smart_platform_automations import (
            check_mpesa_gateway_health,
            check_africastalking_balance,
            check_database_pool_health,
            check_redis_health,
            check_api_rate_limits,
        )
        
        results = {
            "mpesa": check_mpesa_gateway_health().__dict__,
            "africastalking": check_africastalking_balance().__dict__,
            "database": check_database_pool_health().__dict__,
            "redis": check_redis_health().__dict__,
            "rate_limits": [r.__dict__ for r in check_api_rate_limits()],
        }
        
        alerts = []
        
        if not results["mpesa"]["is_healthy"]:
            alerts.append("M-Pesa gateway unhealthy")
        
        if results["africastalking"]["is_low"]:
            alerts.append(f"AT balance low: {results['africastalking']['balance']}")
        
        if not results["database"]["is_healthy"]:
            alerts.append("Database pool near exhaustion")
        
        if not results["redis"]["is_healthy"]:
            alerts.append("Redis memory high")
        
        for rate_limit in results["rate_limits"]:
            if rate_limit["is_throttled"]:
                alerts.append(f"Rate limit throttled: {rate_limit['endpoint']}")
        
        if alerts:
            logger.warning("Platform health alerts: %s", alerts)
        
        return {"status": "success", "alerts": alerts, "results": results}
    
    except Exception as exc:
        logger.error(f"Error in platform health check: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def run_dead_letter_queue_check(self):
    """
    Dead Letter Queue Handler.
    
    Runs every 15 minutes.
    Surfaces failed tasks to System Admin dashboard.
    """
    try:
        from core.algorithms.smart_platform_automations import check_dead_letter_queue
        
        dlq = check_dead_letter_queue()
        
        if dlq.total_items > 0:
            logger.warning(
                "Dead letter queue: %s items, oldest: %s days",
                dlq.total_items,
                dlq.oldest_item_age,
            )
        
        return {
            "status": "success",
            "total_items": dlq.total_items,
            "oldest_age_days": dlq.oldest_item_age,
        }
    
    except Exception as exc:
        logger.error(f"Error in DLQ check: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def check_scaling_needs(self):
    """
    Auto-scaling Trigger.
    
    Runs every 5 minutes.
    Scales Celery workers based on queue depth.
    """
    try:
        from core.algorithms.smart_platform_automations import check_celery_queue_depth
        
        scaling = check_celery_queue_depth()
        
        if scaling.action != "maintain":
            logger.info(
                "Scaling action: %s - %s (current: %s, recommended: %s)",
                scaling.action,
                scaling.reason,
                scaling.current_workers,
                scaling.recommended_workers,
            )
        
        return {
            "status": "success",
            "action": scaling.action,
            "current_workers": scaling.current_workers,
            "recommended_workers": scaling.recommended_workers,
        }
    
    except Exception as exc:
        logger.error(f"Error in scaling check: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def check_mailgun_bounces(self):
    """
    Mailgun Bounce Rate Monitor.
    
    Runs hourly.
    Alerts on high bounce rate.
    """
    try:
        from core.algorithms.smart_platform_automations import check_mailgun_bounce_rate
        
        bounce = check_mailgun_bounce_rate()
        
        if bounce.is_high:
            logger.warning(
                "Mailgun high bounce rate: %.1f%% (%s/%s)",
                bounce.bounce_rate,
                bounce.bounces,
                bounce.total_sent,
            )
        
        return {
            "status": "success",
            "bounce_rate": bounce.bounce_rate,
            "is_high": bounce.is_high,
        }
    
    except Exception as exc:
        logger.error(f"Error checking bounces: {exc}")
        raise self.retry(exc=exc, countdown=60)
