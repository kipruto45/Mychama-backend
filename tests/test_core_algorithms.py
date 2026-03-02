from datetime import UTC, date, datetime, time
from decimal import Decimal

from core.algorithms.analytics import compute_member_activity_cohorts
from core.algorithms.finance import (
    allocate_repayment,
    classify_delinquency,
    compute_par_ratio,
)
from core.algorithms.governance import quorum_required
from core.algorithms.meetings import (
    build_meeting_window,
    detect_overlapping_windows,
)
from core.algorithms.notifications import (
    NotificationPreferenceSnapshot,
    channel_routing,
    in_quiet_hours,
)
from core.algorithms.payments import can_transition_payment_status
from core.algorithms.security import TokenBucketState, consume_from_token_bucket


def test_token_bucket_allows_then_blocks_when_empty():
    now = datetime.now(UTC)
    state = TokenBucketState(tokens=Decimal("1.0"), last_refill_at=now)
    next_state, allowed = consume_from_token_bucket(
        state=state,
        now=now,
        capacity=Decimal("3.0"),
        refill_per_second=Decimal("0.0"),
        cost=Decimal("1.0"),
    )
    assert allowed is True
    assert next_state.tokens == Decimal("0.0")

    final_state, allowed_again = consume_from_token_bucket(
        state=next_state,
        now=now,
        capacity=Decimal("3.0"),
        refill_per_second=Decimal("0.0"),
        cost=Decimal("1.0"),
    )
    assert allowed_again is False
    assert final_state.tokens == Decimal("0.0")


def test_quorum_required_rounds_up():
    assert quorum_required(total_members=9, quorum_percentage=50) == 5
    assert quorum_required(total_members=10, quorum_percentage=50) == 5


def test_meeting_overlap_detection():
    proposed = build_meeting_window(
        start=datetime(2026, 2, 23, 10, 0, tzinfo=UTC),
        duration_minutes=120,
    )
    existing = [
        build_meeting_window(
            start=datetime(2026, 2, 23, 11, 0, tzinfo=UTC),
            duration_minutes=90,
            metadata={"id": "a"},
        ),
        build_meeting_window(
            start=datetime(2026, 2, 23, 15, 0, tzinfo=UTC),
            duration_minutes=60,
            metadata={"id": "b"},
        ),
    ]
    conflicts = detect_overlapping_windows(proposed=proposed, existing=existing)
    assert len(conflicts) == 1
    assert conflicts[0].metadata["id"] == "a"


def test_repayment_allocation_penalty_interest_principal():
    allocation = allocate_repayment(
        amount=Decimal("120.00"),
        penalty_due=Decimal("20.00"),
        interest_due=Decimal("50.00"),
        principal_due=Decimal("200.00"),
        strategy="penalty_interest_principal",
    )
    assert allocation["penalty_paid"] == Decimal("20.00")
    assert allocation["interest_paid"] == Decimal("50.00")
    assert allocation["principal_paid"] == Decimal("50.00")
    assert allocation["unallocated"] == Decimal("0.00")


def test_delinquency_and_par_algorithms():
    assert classify_delinquency(0) == "current"
    assert classify_delinquency(10) == "dpd_1_30"
    assert classify_delinquency(75) == "dpd_61_90"

    par = compute_par_ratio(
        loans=[
            {"outstanding": "1000.00", "days_past_due": 10},
            {"outstanding": "500.00", "days_past_due": 45},
            {"outstanding": "500.00", "days_past_due": 70},
        ],
        days_threshold=30,
    )
    assert par == Decimal("50.00")


def test_payment_transition_rules():
    assert can_transition_payment_status(current="INITIATED", target="PENDING")
    assert can_transition_payment_status(current="PENDING", target="SUCCESS")
    assert not can_transition_payment_status(current="SUCCESS", target="PENDING")


def test_notification_quiet_hours_and_channel_routing():
    now = datetime(2026, 2, 22, 22, 0, tzinfo=UTC)
    assert in_quiet_hours(now=now, start=time(21, 0), end=time(7, 0))
    pref = NotificationPreferenceSnapshot(
        sms_enabled=True,
        email_enabled=False,
        in_app_enabled=True,
        critical_only=True,
    )
    normal_channels = channel_routing(
        requested_channels=["sms", "email", "in_app"],
        preference=pref,
        priority="normal",
    )
    assert normal_channels == ["in_app"]
    critical_channels = channel_routing(
        requested_channels=["sms", "in_app"],
        preference=pref,
        priority="critical",
    )
    assert critical_channels == ["sms", "in_app"]


def test_member_activity_cohorts_matrix():
    matrix = compute_member_activity_cohorts(
        join_month_by_member={
            "u1": date(2026, 1, 5),
            "u2": date(2026, 1, 10),
            "u3": date(2026, 2, 1),
        },
        activity_months_by_member={
            "u1": {date(2026, 1, 20), date(2026, 2, 20)},
            "u2": {date(2026, 1, 22)},
            "u3": {date(2026, 2, 15)},
        },
        horizon_months=2,
    )
    january = matrix["2026-01-01"]
    assert january["cohort_size"] == 2
    assert january["points"][0]["active_members"] == 2
    assert january["points"][1]["active_members"] == 1
