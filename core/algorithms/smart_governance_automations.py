"""
Governance Automations

Production-grade automations for:
- Constitution clause conflict detector
- Election reminder dispatcher
- Resolution library indexer
- Quorum trend analyser
- Term limit enforcer
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from apps.chama.models import Chama, Membership


@dataclass
class ClauseConflict:
    """Constitution clause conflict."""
    existing_clause: str
    existing_text: str
    proposed_clause: str
    proposed_text: str
    conflict_type: str
    severity: str
    description: str


@dataclass
class ConflictCheckResult:
    """Constitution conflict check result."""
    has_conflicts: bool
    conflicts: list[ClauseConflict]
    warnings: list[str]


def check_constitution_clause_conflicts(
    existing_clauses: list[dict],
    proposed_clause_key: str,
    proposed_text: str,
) -> ConflictCheckResult:
    """Check if a proposed clause conflicts with existing constitution."""
    conflicts = []
    warnings = []
    
    CONFLICT_PATTERNS = {
        "quorum": r"(?:quorum|quorate|minimum attendance)",
        "voting": r"(?:vote|voting|threshold|majority)",
        "contribution": r"(?:contribution|amount|schedule|payment)",
        "loan": r"(?:loan|borrowing|credit|eligibility)",
        "payout": r"(?:payout|disbursement|rotation|order)",
        "term": r"(?:term|duration|limit|maximum)",
    }
    
    proposed_lower = proposed_text.lower()
    
    for clause in existing_clauses:
        if clause.get("key") == proposed_clause_key:
            continue
        
        existing_lower = clause.get("text", "").lower()
        clause_key = clause.get("key", "")
        
        matched_pattern = None
        for pattern_name, pattern_regex in CONFLICT_PATTERNS.items():
            if pattern_name in proposed_clause_key.lower():
                matched_pattern = pattern_name
                break
        
        if matched_pattern:
            pattern = CONFLICT_PATTERNS[matched_pattern]
            
            proposed_matches = set(re.findall(pattern, proposed_lower))
            existing_matches = set(re.findall(pattern, existing_lower))
            
            if proposed_matches & existing_matches:
                conflicts.append(ClauseConflict(
                    existing_clause=clause.get("key", ""),
                    existing_text=clause.get("text", "")[:100],
                    proposed_clause=proposed_clause_key,
                    proposed_text=proposed_text[:100],
                    conflict_type=matched_pattern,
                    severity="HIGH" if matched_pattern in ["quorum", "voting"] else "MEDIUM",
                    description=f"Both clauses address {matched_pattern}. Review for consistency.",
                ))
    
    if not conflicts:
        existing_lower = proposed_text.lower()
        for other_key, pattern in CONFLICT_PATTERNS.items():
            if other_key in proposed_clause_key.lower():
                continue
            
            if re.search(pattern, existing_lower):
                warnings.append(
                    f"Proposed clause may affect existing {other_key} rules."
                )
    
    return ConflictCheckResult(
        has_conflicts=len(conflicts) > 0,
        conflicts=conflicts,
        warnings=warnings,
    )


@dataclass
class ElectionReminder:
    """Election reminder details."""
    chama_id: str
    election_type: str
    reminder_type: str
    days_until: int
    recipients: list[str]
    message: str


def get_election_reminders(chama: "Chama") -> list[ElectionReminder]:
    """Get election reminders for a chama."""
    from apps.governance.models import Election, ElectionStatus
    from apps.chama.models import Membership, MembershipRole, MembershipStatus
    
    reminders = []
    today = timezone.now().date()
    
    leadership_roles = [
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
    ]
    
    upcoming_elections = Election.objects.filter(
        chama=chama,
        status__in=[ElectionStatus.SCHEDULED, ElectionStatus.NOMINATION],
    ).select_related("chama")
    
    for election in upcoming_elections:
        days_until = (election.election_date - today).days
        
        if days_until == 30:
            reminders.append(ElectionReminder(
                chama_id=str(chama.id),
                election_type=election.election_type,
                reminder_type="nomination_open",
                days_until=days_until,
                recipients=[str(r) for r in leadership_roles],
                message=f"Nomination period for {election.election_type} election opens in 30 days.",
            ))
        elif days_until == 7:
            reminders.append(ElectionReminder(
                chama_id=str(chama.id),
                election_type=election.election_type,
                reminder_type="election_soon",
                days_until=days_until,
                recipients=[str(r) for r in leadership_roles],
                message=f"Election for {election.election_type} positions in 7 days. Vote!",
            ))
        elif days_until == 1:
            reminders.append(ElectionReminder(
                chama_id=str(chama.id),
                election_type=election.election_type,
                reminder_type="election_tomorrow",
                days_until=days_until,
                recipients=["all"],
                message=f"Election tomorrow! All members please vote.",
            ))
    
    return reminders


@dataclass
class Resolution:
    """Indexed resolution."""
    resolution_id: str
    title: str
    category: str
    passed_date: date
    keywords: list[str]


@dataclass
class ResolutionIndex:
    """Resolution library index."""
    chama_id: str
    resolutions: list[Resolution]
    last_updated: date


def index_resolutions(chama: "Chama") -> ResolutionIndex:
    """Index all resolutions for a chama for search."""
    from apps.governance.models import Resolution as ResolutionModel, ResolutionStatus
    
    today = timezone.now().date()
    
    resolutions = ResolutionModel.objects.filter(
        chama=chama,
        status=ResolutionStatus.PASSED,
    ).order_by("-created_at")
    
    indexed_resolutions = []
    
    for resolution in resolutions:
        keywords = []
        
        title_words = resolution.title.lower().split()
        keywords.extend([w for w in title_words if len(w) > 3])
        
        text_words = resolution.text.lower().split()
        keywords.extend([w for w in text_words if len(w) > 4][:20])
        
        category = resolution.category or "general"
        
        indexed_resolutions.append(Resolution(
            resolution_id=str(resolution.id),
            title=resolution.title,
            category=category,
            passed_date=resolution.created_at.date() if resolution.created_at else today,
            keywords=list(set(keywords)),
        ))
    
    return ResolutionIndex(
        chama_id=str(chama.id),
        resolutions=indexed_resolutions,
        last_updated=today,
    )


@dataclass
class QuorumTrend:
    """Quorum attendance trend."""
    chama_id: str
    meeting_count: int
    average_attendance: float
    quorum_rate: float
    trend_direction: str
    alert_level: str
    message: str


def analyze_quorum_trend(chama: "Chama", months: int = 6) -> QuorumTrend:
    """Analyze meeting attendance quorum trend for a chama."""
    from apps.meetings.models import Meeting
    from apps.governance.models import Chama as ChamaModel
    
    today = timezone.now().date()
    lookback_date = today - timedelta(days=months * 30)
    
    meetings = Meeting.objects.filter(
        chama=chama,
        date__gte=lookback_date,
    ).select_related("chama")
    
    total_meetings = meetings.count()
    
    if total_meetings == 0:
        return QuorumTrend(
            chama_id=str(chama.id),
            meeting_count=0,
            average_attendance=0.0,
            quorum_rate=0.0,
            trend_direction="stable",
            alert_level="NONE",
            message="No meetings found in analysis period.",
        )
    
    required_quorum = chama.chama.quorum_percentage or 50
    
    attendance_rates = []
    for meeting in meetings:
        total_members = meeting.chama.memberships.filter(is_active=True).count()
        if total_members > 0:
            rate = (meeting.attendance_records.filter(status="present").count() / total_members) * 100
            attendance_rates.append(rate)
    
    if not attendance_rates:
        return QuorumTrend(
            chama_id=str(chama.id),
            meeting_count=total_meetings,
            average_attendance=0.0,
            quorum_rate=0.0,
            trend_direction="stable",
            alert_level="NONE",
            message="No attendance records found.",
        )
    
    avg_attendance = sum(attendance_rates) / len(attendance_rates)
    quorum_met_count = sum(1 for r in attendance_rates if r >= required_quorum)
    quorum_rate = (quorum_met_count / len(attendance_rates)) * 100
    
    trend = "stable"
    if len(attendance_rates) >= 3:
        recent = sum(attendance_rates[-3:]) / 3
        older = sum(attendance_rates[:-3]) / max(1, len(attendance_rates) - 3)
        
        if recent < older * 0.9:
            trend = "declining"
        elif recent > older * 1.1:
            trend = "improving"
    
    alert = "NONE"
    if quorum_rate < 50:
        alert = "HIGH"
    elif quorum_rate < 70:
        alert = "MEDIUM"
    elif quorum_rate < 85:
        alert = "LOW"
    
    return QuorumTrend(
        chama_id=str(chama.id),
        meeting_count=total_meetings,
        average_attendance=avg_attendance,
        quorum_rate=quorum_rate,
        trend_direction=trend,
        alert_level=alert,
        message=f"Attendance {'declining' if trend == 'declining' else 'stable'}. "
                f"Quorum met in {quorum_met_count}/{total_meetings} meetings.",
    )


@dataclass
class TermLimitStatus:
    """Term limit status."""
    member_id: str
    member_name: str
    role: str
    current_term_start: date
    term_end_date: date
    days_remaining: int
    is_expiring_soon: bool
    alert_level: str
    message: str


def check_term_limits(chama: "Chama") -> list[TermLimitStatus]:
    """Check term limits for leadership positions."""
    from apps.chama.models import Membership, MembershipRole, MembershipStatus
    
    today = timezone.now().date()
    warning_threshold = 30
    
    statuses = []
    
    leadership_positions = [
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
    ]
    
    leadership_members = Membership.objects.filter(
        chama=chama,
        role__in=leadership_positions,
        status=MembershipStatus.ACTIVE,
        is_active=True,
    ).select_related("user")
    
    for membership in leadership_members:
        if not membership.role_started_at:
            continue
        
        term_duration_days = 365
        term_end = membership.role_started_at.date() + timedelta(days=term_duration_days)
        days_remaining = (term_end - today).days
        
        is_expiring_soon = days_remaining <= warning_threshold
        
        alert = "NONE"
        if days_remaining <= 0:
            alert = "HIGH"
            message = f"Term expired. Election required."
        elif days_remaining <= warning_threshold:
            alert = "MEDIUM"
            message = f"Term expires in {days_remaining} days. Prepare for election."
        else:
            message = f"Term ends {term_end.strftime('%B %d, %Y')}."
        
        statuses.append(TermLimitStatus(
            member_id=str(membership.id),
            member_name=membership.user.full_name if membership.user else "Unknown",
            role=membership.role,
            current_term_start=membership.role_started_at.date(),
            term_end_date=term_end,
            days_remaining=max(0, days_remaining),
            is_expiring_soon=is_expiring_soon,
            alert_level=alert,
            message=message,
        ))
    
    return sorted(statuses, key=lambda x: x.days_remaining)