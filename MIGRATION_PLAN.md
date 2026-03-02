# Django Backend Migration Plan

## Overview

This document outlines the migration plan for updating the Chama system Django backend with new features, improvements, and data management utilities.

---

## 1. Migration Commands

### Available Commands

#### 1.1 Seed Development Data
```bash
# Basic usage - creates 3 chamas with 10 members each
python manage.py seed_dev_data

# Full options
python manage.py seed_dev_data --chamas=5 --members-per-chama=20 --with-loans --with-contributions --clean
```

**Options:**
- `--chamas`: Number of chamas to create (default: 3)
- `--members-per-chama`: Members per chama (default: 10)
- `--with-loans`: Create sample loans
- `--with-contributions`: Create sample contributions
- `--clean`: Clean existing test data first

#### 1.2 Backfill Wallets
```bash
# Dry run to see what would be created
python manage.py backfill_wallets --dry-run

# Actually create missing wallets
python manage.py backfill_wallets

# Specific options
python manage.py backfill_wallets --chamas-only --currency=KES
python manage.py backfill_wallets --users-only
```

**Options:**
- `--dry-run`: Show what would be created without making changes
- `--chamas-only`: Only create chama wallets
- `--users-only`: Only create user wallets
- `--currency`: Default currency (default: KES)

#### 1.3 Reconcile M-Pesa
```bash
# Reconcile last 7 days
python manage.py reconcile_mpesa

# Reconcile specific number of days
python manage.py reconcile_mpesa --days=30

# Dry run to see discrepancies
python manage.py reconcile_mpesa --dry-run

# Automatically fix discrepancies
python manage.py reconcile_mpesa --fix

# Specific chama
python manage.py reconcile_mpesa --chama-id=<uuid> --verbose
```

**Options:**
- `--days`: Number of days to reconcile (default: 7)
- `--chama-id`: Specific chama UUID to reconcile
- `--dry-run`: Show discrepancies without fixing
- `--fix`: Automatically fix discrepancies
- `--verbose`: Show detailed output

---

## 2. New API Response Format

### Standard Success Response
```json
{
  "success": true,
  "data": { ... },
  "message": "Optional message",
  "meta": { ... }
}
```

### Standard Error Response
```json
{
  "success": false,
  "error": {
    "message": "Error description",
    "code": "ERROR_CODE",
    "details": { ... }
  }
}
```

### Paginated Response
```json
{
  "success": true,
  "data": [ ... ],
  "pagination": {
    "limit": 20,
    "offset": 0,
    "total": 150,
    "next_cursor": "abc123"
  }
}
```

---

## 3. Role-Based Access Control Matrix

### Finance Permissions

| Role | Record Contribution | View All Contributions | Request Loan | Approve Loan | Disburse Loan | View Reports |
|------|---------------------|------------------------|--------------|--------------|---------------|--------------|
| MEMBER | ✗ | ✗ (own only) | ✓ | ✗ | ✗ | ✗ |
| TREASURER | ✓ | ✓ | ✗ | ✓ | ✗* | ✓ |
| SECRETARY | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ |
| AUDITOR | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ |
| CHAMA_ADMIN | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |
| ADMIN | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |
| SUPERADMIN | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

*Treasure can only disburse if loan product doesn't require separate disburser.

---

## 4. Database Changes Required

### New Fields/Indexes

1. **Wallet.owner_id** - Currently PositiveIntegerField, needs to support UUID
   - Status: Requires migration
   - Recommendation: Add owner_id_uuid field for migration

2. **LedgerEntry.wallet** - Already added but may need backfill

### Existing Models (No Changes Needed)

- User, Membership, Chama - Well structured
- Loan, LoanProduct, Contribution - Comprehensive
- LedgerEntry - Has idempotency keys
- All enums properly defined

---

## 5. Recommended Migration Steps

### Step 1: Run Database Migrations
```bash
# Ensure all migrations are applied
python manage.py migrate
```

### Step 2: Backfill Wallets
```bash
# Create missing wallets
python manage.py backfill_wallets
```

### Step 3: Reconcile M-Pesa
```bash
# Reconcile recent transactions
python manage.py reconcile_mpesa --days=30 --fix
```

### Step 4: Verify Data
```bash
# Check wallet balances match ledger
# Run reports and verify totals
```

---

## 6. Verification Checklist

### Model Verification
- [ ] All models have proper UUID primary keys
- [ ] All foreign keys have proper on_delete behavior
- [ ] Indexes exist for frequently queried fields
- [ ] Constraints are in place for data integrity

### Business Rules Verification
- [ ] Loan eligibility checks work correctly
- [ ] Loan approval workflow functions properly
- [ ] Ledger entries are append-only
- [ ] Idempotency keys prevent duplicate transactions
- [ ] Month closure prevents backdated entries

### API Response Verification
- [ ] All endpoints return standardized format
- [ ] Pagination works correctly
- [ ] Error responses are consistent
- [ ] Authentication and permissions enforced

### Permission Verification
- [ ] Role matrix enforced correctly
- [ ] Members can only see their own data where appropriate
- [ ] Sensitive operations require appropriate roles

### Data Integrity Verification
- [ ] Wallet balances match ledger totals
- [ ] M-Pesa transactions have corresponding ledger entries
- [ ] No orphaned records in related models

---

## 7. Rollback Plan

If issues arise during migration:

1. **Database**: Use Django's `migrate <app> <migration_name>` to roll back
2. **Data**: Restore from backup if data corruption occurs
3. **Code**: Git revert to previous working state

---

## 8. Support Commands

### Check System Health
```bash
# List all chamas
python manage.py shell -c "from apps.chama.models import Chama; print(Chama.objects.count())"

# List all users
python manage.py shell -c "from apps.accounts.models import User; print(User.objects.count())"

# Check wallet totals
python manage.py shell -c "from apps.finance.models import Wallet; from django.db.models import Sum; print(Wallet.objects.aggregate(Sum('available_balance')))"
```

### Debug Issues
```bash
# Check recent ledger entries
python manage.py shell -c "from apps.finance.models import LedgerEntry; print(LedgerEntry.objects.order_by('-created_at')[:10])"

# Check failed payments
python manage.py shell -c "from apps.payments.models import MpesaTransaction; print(MpesaTransaction.objects.filter(status='failed').count())"
```
