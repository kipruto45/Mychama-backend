# Payout Workflow Implementation

## Overview

The Payout Workflow implements a complete rotation-based payout system for MyChama chamas. Members take turns receiving payouts from the pooled funds, with strict eligibility checks, multi-level approvals (Treasurer → Chairperson), and multiple payment methods.

## Architecture

### Core Models

#### **Payout**
The main model representing a single payout instance. Tracks:
- Rotation position and cycle
- Workflow status (triggered → approved → success/failed)
- Eligibility status and issues
- Approval tracking (treasurer, chairperson)
- Payment processing (method, intent, retries)
- On-hold tracking with flagging capability
- Audit trail with immutable logs

**Status Lifecycle:**
```
TRIGGERED
    ↓
ROTATION_CHECK
    ↓
ELIGIBILITY_CHECK ─→ INELIGIBLE (skip/defer)
    ↓
AWAITING_TREASURER_REVIEW ─→ TREASURY_REJECTED
    ↓
AWAITING_CHAIR_APPROVAL ─→ CHAIR_REJECTED
    ↓
APPROVED
    ↓
PROCESSING
    ↓
SUCCESS / FAILED (with retry capability)
```

#### **PayoutRotation**
Tracks rotation state for each chama:
- Current position in rotation queue
- Rotation cycle number
- Ordered list of member IDs
- Last completed payout

#### **PayoutEligibilityCheck**
Immutable record of eligibility check results:
- Penalties check (outstanding penalties)
- Disputes check (active issues)
- Loans check (overdue loans)
- Member status check
- Wallet balance check

#### **PayoutAuditLog**
Immutable audit trail for all state changes:
- Action performed (TRIGGERED, APPROVED, etc.)
- Actor (user who performed action)
- Previous/new status
- Reason and details

### Workflow Steps

#### 1. **Trigger Payout**
```python
PayoutService.trigger_payout(
    chama_id=chama.id,
    member_id=member.id,  # Optional, uses rotation if omitted
    amount=5000,
    trigger_type="manual",  # or "auto"
    triggered_by_id=user.id
)
```

- Creates new Payout instance
- Sets initial status to TRIGGERED
- Records audit log
- Sends notifications to relevant parties

#### 2. **Eligibility Check**
```python
payout, eligibility_check = PayoutService.check_eligibility(payout.id)
```

Checks:
- ✓ Member is active
- ✓ No outstanding penalties
- ✓ No active disputes
- ✓ No overdue loans
- ✓ Wallet has sufficient funds (for wallet payouts)

If all checks pass → `AWAITING_TREASURER_REVIEW`
Otherwise → `INELIGIBLE` with reason

**Handling Ineligible Members:**
- **Skip**: Move to next member in rotation
- **Defer**: Keep same member for next cycle

#### 3. **Treasurer Review**
```python
# Send for review
PayoutService.send_to_treasurer_review(payout.id)

# Treasurer approves
PayoutService.treasurer_approve(payout.id, treasurer_user.id)

# Treasurer rejects
PayoutService.treasurer_reject(payout.id, "Reason", treasurer_user.id)
```

Treasurer reviews:
- Member name and amount
- Rotation position
- Eligibility status
- Previous rejection reasons (if any)

On approval → Moves to `AWAITING_CHAIR_APPROVAL`
On rejection → `TREASURY_REJECTED` (can retry)

#### 4. **Chairperson Approval**
```python
# Chairperson approves
PayoutService.chairperson_approve(payout.id, chair_user.id)

# Chairperson rejects
PayoutService.chairperson_reject(payout.id, "Reason", chair_user.id)
```

Final approval from chairperson.

On approval → `APPROVED` → Payment processing begins
On rejection → `CHAIR_REJECTED` (can be retried)

#### 5. **Payment Processing**
```python
payment_intent = PayoutService.initiate_payment(payout.id)
```

Supported payment methods:
- **M-Pesa B2C**: Direct to member's M-Pesa account
- **Bank Transfer**: Via bank API to registered account
- **Wallet**: Instant credit to member's chama wallet

Payment flow:
1. Create PaymentIntent
2. Initiate provider-specific payment (async)
3. Receive callback from provider
4. Update payment status
5. On success: Update ledger, advance rotation, generate receipt
6. On failure: Notify treasurer, allow retry (max 3 attempts)

#### 6. **On-Hold Flagging**
Treasurer can flag payout on hold for issues:
```python
PayoutService.flag_payout_on_hold(
    payout.id,
    "Awaiting member to update bank details",
    treasurer_user.id
)

# Release when issue resolved
PayoutService.release_payout_from_hold(
    payout.id,
    treasurer_user.id,
    "Bank details updated"
)
```

#### 7. **Completion**
On successful payment:
- Update payout status to SUCCESS
- Create ledger entry (double-entry accounting)
- Update rotation (advance to next member)
- Generate receipt
- Send notifications to member and treasurer

On failure:
- Payout status becomes FAILED
- Can retry up to 3 times
- Treasurer notified and can choose different payment method

### Service Methods

#### PayoutService

**Static Methods (Primary API):**

| Method | Purpose |
|--------|---------|
| `trigger_payout()` | Create new payout |
| `check_eligibility()` | Run eligibility checks |
| `skip_to_next_member()` | Skip ineligible member |
| `defer_to_next_cycle()` | Defer to next cycle |
| `send_to_treasurer_review()` | Move to treasurer stage |
| `treasurer_approve()` | Treasurer approves |
| `treasurer_reject()` | Treasurer rejects |
| `chairperson_approve()` | Chairperson approves |
| `chairperson_reject()` | Chairperson rejects |
| `initiate_payment()` | Start payment processing |
| `handle_payment_success()` | Handle successful payment |
| `handle_payment_failure()` | Handle payment failure |
| `flag_payout_on_hold()` | Flag for manual review |
| `release_payout_from_hold()` | Release from hold |
| `retry_failed_payout()` | Retry failed payment |

### API Endpoints

#### PayoutViewSet
Base: `/api/v1/payouts/`

**Endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/payouts/` | GET | List payouts |
| `/payouts/{id}/` | GET | Get payout details |
| `/payouts/trigger_payout/` | POST | Trigger new payout |
| `/payouts/{id}/send_to_review/` | POST | Send to treasurer review |
| `/payouts/{id}/treasurer_approve/` | POST | Treasurer approves |
| `/payouts/{id}/treasurer_reject/` | POST | Treasurer rejects |
| `/payouts/{id}/chairperson_approve/` | POST | Chairperson approves |
| `/payouts/{id}/chairperson_reject/` | POST | Chairperson rejects |
| `/payouts/{id}/set_payout_method/` | POST | Change payment method |
| `/payouts/{id}/flag_hold/` | POST | Flag on hold |
| `/payouts/{id}/release_hold/` | POST | Release from hold |
| `/payouts/{id}/retry_payment/` | POST | Retry failed payment |

#### PayoutRotationViewSet
Base: `/api/v1/rotations/`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/rotation/{chama_id}/` | GET | Get rotation for chama |

### Request/Response Examples

#### Trigger Payout
```python
POST /api/v1/payouts/trigger_payout/

{
    "chama_id": "uuid",
    "member_id": "uuid",  # Optional
    "amount": 5000,       # Optional
    "trigger_type": "manual"
}

Response (201):
{
    "id": "payout-uuid",
    "chama": "chama-uuid",
    "member": {
        "id": "membership-uuid",
        "user": {"phone": "+254..."},
        ...
    },
    "amount": 5000,
    "status": "eligibility_check",
    "eligibility_status": "eligible",
    ...
}
```

#### Chairperson Approve
```python
POST /api/v1/payouts/{payout_id}/chairperson_approve/

Response (200):
{
    "id": "payout-uuid",
    "status": "approved",
    "chairperson_approved_at": "2026-04-25T10:30:00Z",
    ...
}
```

#### Flag Hold
```python
POST /api/v1/payouts/{payout_id}/flag_hold/

{
    "reason": "Awaiting member to update bank details"
}

Response (200):
{
    "status": "hold",
    "is_on_hold": true,
    "hold_reason": "Awaiting member to update bank details",
    ...
}
```

### Security & Compliance

#### Authorization
- Treasurer-only operations: `send_to_review`, `treasurer_approve`, `flag_hold`, `release_hold`
- Chairperson-only: `chairperson_approve`, `chairperson_reject`
- All operations server-side role-checked against membership

#### Audit Trail
- Every action recorded in `PayoutAuditLog` (immutable)
- Includes actor, action, status change, reason, details
- Useful for compliance and debugging

#### Financial Controls
- All state changes wrapped in `@transaction.atomic` for consistency
- Double-entry ledger entries for all successful payouts
- Idempotency keys prevent duplicate payments
- Max 3 retry attempts per payment (configurable)

#### Data Security
- Sensitive information (phone numbers) redacted in generic responses
- Detailed info only for authorized users
- No sensitive data in logs

### Notifications

Payout events trigger notifications via `NotificationService`:

| Event | Recipient | Channels |
|-------|-----------|----------|
| Triggered | Treasurer | PUSH, IN_APP |
| Awaiting Treasurer Review | Treasurer | PUSH, IN_APP |
| Awaiting Chair Approval | Chairperson | PUSH, IN_APP |
| Rejected (Treasurer) | Member | PUSH, IN_APP |
| Rejected (Chairperson) | Member | PUSH, IN_APP |
| Payment Success | Member, Treasurer | PUSH, IN_APP, SMS |
| Payment Failed | Treasurer | PUSH, IN_APP |

### Async Tasks (Celery)

#### `process_pending_payouts`
Run periodically to move eligible payouts through workflow.

```python
@shared_task
def process_pending_payouts():
    """Process all pending payouts."""
```

#### `retry_failed_payouts`
Automatically retry failed payments up to max retries.

```python
@shared_task
def retry_failed_payouts():
    """Retry failed payouts."""
```

#### `generate_payout_receipts`
Generate PDF receipts for completed payouts.

```python
@shared_task
def generate_payout_receipts():
    """Generate receipts for completed payouts."""
```

#### `send_payout_reminders`
Send contribution reminders to members before rotation payout.

```python
@shared_task
def send_payout_reminders():
    """Send contribution reminders."""
```

### Database Schema

#### Key Indexes
- `chama, status, created_at` - Fast status queries per chama
- `member, status` - Member payout history
- `status, created_at` - Timeline queries
- `rotation_cycle, rotation_position` - Rotation lookups

#### Constraints
- `payout_amount_positive` - Amount must be > 0
- `uniq_payment_intent_idempotency_per_chama` - Prevent duplicates (in payments app)

### Testing

Comprehensive test suite in `tests.py`:

```python
class PayoutServiceTestCase(TestCase):
    def test_trigger_payout()
    def test_eligibility_check_eligible()
    def test_eligibility_check_with_penalties()
    def test_eligibility_check_inactive_member()
    def test_skip_to_next_member()
    def test_defer_to_next_cycle()
    def test_treasurer_approval_flow()
    def test_treasurer_rejection()
    def test_chairperson_approval_flow()
    def test_chairperson_rejection()
    def test_flag_and_release_hold()
    def test_payment_success_workflow()
    def test_payment_failure_and_retry()
    def test_rotation_advancement()
```

Run tests:
```bash
python manage.py test apps.payouts
```

### Migration

Run migrations to create tables:

```bash
python manage.py makemigrations
python manage.py migrate
```

### Configuration

The payout system respects these settings (configurable):

| Setting | Default | Purpose |
|---------|---------|---------|
| `PAYOUT_MAX_RETRIES` | 3 | Max payment retry attempts |
| `PAYOUT_RECEIPT_GENERATION` | true | Auto-generate receipts |
| `PAYOUT_NOTIFICATION_ENABLED` | true | Send notifications |

### Future Enhancements

1. **Batched Payouts** - Process multiple members in parallel
2. **Scheduled Payouts** - Auto-trigger based on schedule/rules
3. **Conditional Amounts** - Calculate payout amount from rules
4. **Appeals Process** - Allow members to appeal rejection
5. **Partial Payouts** - Support partial amounts with scheduling
6. **Multi-Currency** - Support different currencies per payout
7. **Payout Analytics** - Dashboard with metrics and trends
8. **Provider Reconciliation** - Auto-reconcile with payment providers

### Troubleshooting

#### Payout stuck in "processing"
- Check payment provider logs
- Retry payment via `retry_payment` endpoint
- Or flag on hold and investigate

#### Eligibility check failing unexpectedly
- Review `eligibility_issues` in payout detail
- Check `PayoutEligibilityCheck` record for details
- Verify member status, penalties, loans, disputes

#### Rotation not advancing
- Ensure payout payment succeeded (status = SUCCESS)
- Check that rotation exists for chama
- Verify `PayoutRotation.members_in_rotation` is populated

### Rollout Checklist

- [ ] Migrations applied successfully
- [ ] Payouts app registered in INSTALLED_APPS
- [ ] URLs configured in main API
- [ ] Notification types created
- [ ] Celery tasks scheduled
- [ ] Initial rotation data created
- [ ] Treasurer and Chairperson trained
- [ ] Test end-to-end workflow
- [ ] Monitor logs for errors
- [ ] Document in member handbook
