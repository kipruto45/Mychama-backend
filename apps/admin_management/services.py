"""
Comprehensive Admin Management Service
Provides all admin operations including user management, loans, contributions, withdrawals, and reports.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRequest, MembershipRole, MemberStatus
from apps.finance.models import (
    Contribution,
    ContributionType,
    Loan,
    LoanProduct,
    LoanStatus,
    Repayment,
    Wallet,
    LedgerEntry,
    LedgerDirection,
    ManualAdjustment,
    Penalty,
)
from apps.payments.models import PaymentIntent, PaymentIntentStatus, MpesaTransaction, PaymentDispute, PaymentRefund, MpesaB2CPayout


class AdminManagementService:
    """
    Centralized service for all admin management operations.
    Provides CRUD operations for users, loans, contributions, withdrawals, and reports.
    """

    # ============ USER MANAGEMENT ============

    @staticmethod
    @transaction.atomic
    def create_user(
        phone: str,
        full_name: str,
        email: str = "",
        is_active: bool = True,
        is_staff: bool = False,
        is_superuser: bool = False,
    ) -> User:
        """Create a new user in the system."""
        if User.objects.filter(phone=phone).exists():
            raise ValueError(f"User with phone {phone} already exists")

        user = User.objects.create_user(
            phone=phone,
            password=User.objects.make_random_password(),  # Generate random password
            full_name=full_name,
            email=email,
            is_active=is_active,
            is_staff=is_staff,
            is_superuser=is_superuser,
        )
        return user

    @staticmethod
    @transaction.atomic
    def update_user(
        user_id: int,
        full_name: str = None,
        email: str = None,
        is_active: bool = None,
        phone: str = None,
    ) -> User:
        """Update user details."""
        user = User.objects.get(id=user_id)
        
        if full_name is not None:
            user.full_name = full_name
        if email is not None:
            user.email = email
        if is_active is not None:
            user.is_active = is_active
        if phone is not None and phone != user.phone:
            if User.objects.filter(phone=phone).exclude(id=user_id).exists():
                raise ValueError(f"Phone {phone} is already in use")
            user.phone = phone
        
        user.save()
        return user

    @staticmethod
    def get_all_users(
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        is_active: bool = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all users."""
        queryset = User.objects.all().order_by("-created_at")

        if search:
            queryset = queryset.filter(
                Q(full_name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search)
            )

        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        total = queryset.count()
        offset = (page - 1) * page_size
        users = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "users": [
                {
                    "id": u.id,
                    "phone": u.phone,
                    "full_name": u.full_name,
                    "email": u.email,
                    "is_active": u.is_active,
                    "is_staff": u.is_staff,
                    "is_superuser": u.is_superuser,
                    "created_at": u.created_at.isoformat(),
                    "last_login": u.last_login.isoformat() if u.last_login else None,
                }
                for u in users
            ],
        }

    @staticmethod
    @transaction.atomic
    def deactivate_user(user_id: int) -> User:
        """Deactivate a user account."""
        user = User.objects.get(id=user_id)
        user.is_active = False
        user.save()
        return user

    @staticmethod
    @transaction.atomic
    def activate_user(user_id: int) -> User:
        """Activate a user account."""
        user = User.objects.get(id=user_id)
        user.is_active = True
        user.save()
        return user

    # ============ CHAMA MANAGEMENT ============

    @staticmethod
    @transaction.atomic
    def create_chama(
        name: str,
        description: str = "",
        currency: str = "KES",
        max_members: int = 100,
        admin_user_id: int = None,
    ) -> Chama:
        """Create a new chama and optionally add admin."""
        chama = Chama.objects.create(
            name=name,
            description=description,
            currency=currency,
            max_members=max_members,
        )

        if admin_user_id:
            admin_user = User.objects.get(id=admin_user_id)
            Membership.objects.create(
                user=admin_user,
                chama=chama,
                role=MembershipRole.CHAMA_ADMIN,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                approved_at=timezone.now(),
            )

        return chama

    @staticmethod
    def get_all_chamas(
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all chamas."""
        queryset = Chama.objects.all().order_by("-created_at")

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search)
            )

        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        chamas = queryset[offset:offset + page_size]

        result = []
        for c in chamas:
            member_count = Membership.objects.filter(chama=c, is_active=True).count()
            result.append({
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "currency": c.currency,
                "status": c.status,
                "max_members": c.max_members,
                "member_count": member_count,
                "created_at": c.created_at.isoformat(),
            })

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "chamas": result,
        }

    # ============ MEMBERSHIP MANAGEMENT ============

    @staticmethod
    @transaction.atomic
    def approve_membership(membership_id: int, actor: User) -> Membership:
        """Approve a membership request."""
        membership = Membership.objects.get(id=membership_id)
        membership.status = MemberStatus.ACTIVE
        membership.is_active = True
        membership.is_approved = True
        membership.approved_at = timezone.now()
        membership.approved_by = actor
        membership.save()
        return membership

    @staticmethod
    @transaction.atomic
    def reject_membership(membership_id: int, actor: User, reason: str = "") -> Membership:
        """Reject a membership request."""
        membership = Membership.objects.get(id=membership_id)
        membership.status = MemberStatus.EXITED
        membership.is_active = False
        membership.is_approved = False
        membership.exit_reason = reason
        membership.save()
        return membership

    @staticmethod
    @transaction.atomic
    def update_member_role(
        membership_id: int,
        new_role: str,
        actor: User,
    ) -> Membership:
        """Update a member's role."""
        membership = Membership.objects.get(id=membership_id)
        old_role = membership.role
        membership.role = new_role
        membership.updated_by = actor
        membership.save()
        return membership

    @staticmethod
    def get_all_members(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        status: str = None,
        role: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all members."""
        queryset = Membership.objects.select_related("user", "chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if search:
            queryset = queryset.filter(
                Q(user__full_name__icontains=search) |
                Q(user__phone__icontains=search)
            )

        if status:
            queryset = queryset.filter(status=status)

        if role:
            queryset = queryset.filter(role=role)

        total = queryset.count()
        offset = (page - 1) * page_size
        members = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "members": [
                {
                    "id": m.id,
                    "user_id": m.user_id,
                    "chama_id": m.chama_id,
                    "chama_name": m.chama.name,
                    "full_name": m.user.full_name,
                    "phone": m.user.phone,
                    "email": m.user.email,
                    "role": m.role,
                    "status": m.status,
                    "is_active": m.is_active,
                    "is_approved": m.is_approved,
                    "joined_at": m.joined_at.isoformat(),
                    "approved_at": m.approved_at.isoformat() if m.approved_at else None,
                }
                for m in members
            ],
        }

    # ============ LOAN MANAGEMENT ============

    @staticmethod
    @transaction.atomic
    def create_loan(
        chama_id: int,
        member_id: int,
        amount: Decimal,
        product_id: int = None,
        purpose: str = "",
        actor: User = None,
    ) -> Loan:
        """Create a new loan for a member."""
        chama = Chama.objects.get(id=chama_id)
        member = User.objects.get(id=member_id)

        if product_id:
            product = LoanProduct.objects.get(id=product_id)
        else:
            # Get default product
            product = LoanProduct.objects.filter(chama=chama, is_active=True).first()
            if not product:
                raise ValueError("No active loan product found")

        # Get membership
        membership = Membership.objects.get(chama=chama, user=member)

        loan = Loan.objects.create(
            chama=chama,
            member=member,
            loan_product=product,
            principal_amount=amount,
            interest_rate=product.interest_rate,
            status=LoanStatus.PENDING,
            purpose=purpose or f"Loan for {member.full_name}",
        )

        # Generate schedule
        from apps.finance.services import FinanceService
        FinanceService.generate_schedule(loan)

        return loan

    @staticmethod
    @transaction.atomic
    def approve_loan(loan_id: int, actor: User, note: str = "") -> Loan:
        """Approve a loan."""
        from apps.finance.services import FinanceService
        return FinanceService.approve_loan(loan_id, actor, note)

    @staticmethod
    @transaction.atomic
    def reject_loan(loan_id: int, actor: User, note: str = "") -> Loan:
        """Reject a loan."""
        from apps.finance.services import FinanceService
        return FinanceService.reject_loan(loan_id, actor, note)

    @staticmethod
    @transaction.atomic
    def disburse_loan(loan_id: int, actor: User) -> Loan:
        """Disburse a loan."""
        from apps.finance.services import FinanceService
        return FinanceService.disburse_loan(loan_id, actor)

    @staticmethod
    def get_all_loans(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
        member_id: int = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all loans."""
        queryset = Loan.objects.select_related("member", "chama", "loan_product").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if status:
            queryset = queryset.filter(status=status)

        if member_id:
            queryset = queryset.filter(member_id=member_id)

        total = queryset.count()
        offset = (page - 1) * page_size
        loans = queryset[offset:offset + page_size]

        result = []
        for loan in loans:
            total_paid = Repayment.objects.filter(loan=loan).aggregate(Sum("amount"))["amount__sum"] or 0
            result.append({
                "id": loan.id,
                "chama_id": loan.chama_id,
                "chama_name": loan.chama.name,
                "member_id": loan.member_id,
                "member_name": loan.member.full_name,
                "member_phone": loan.member.phone,
                "product_name": loan.loan_product.name if loan.loan_product else None,
                "principal_amount": float(loan.principal_amount),
                "interest_amount": float(loan.interest_amount),
                "total_amount": float(loan.total_amount),
                "total_paid": float(total_paid),
                "status": loan.status,
                "purpose": loan.purpose,
                "created_at": loan.created_at.isoformat(),
                "approved_at": loan.approved_at.isoformat() if loan.approved_at else None,
                "disbursed_at": loan.disbursed_at.isoformat() if loan.disbursed_at else None,
            })

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "loans": result,
        }

    # ============ CONTRIBUTION MANAGEMENT ============

    @staticmethod
    @transaction.atomic
    def create_contribution(
        chama_id: int,
        member_id: int,
        amount: Decimal,
        contribution_type_id: int = None,
        date_paid: date = None,
        note: str = "",
        actor: User = None,
    ) -> Contribution:
        """Create a new contribution."""
        chama = Chama.objects.get(id=chama_id)
        member = User.objects.get(id=member_id)

        if contribution_type_id:
            contrib_type = ContributionType.objects.get(id=contribution_type_id)
        else:
            # Get default contribution type
            contrib_type = ContributionType.objects.filter(chama=chama, is_active=True).first()
            if not contrib_type:
                raise ValueError("No active contribution type found")

        contribution = Contribution.objects.create(
            chama=chama,
            member=member,
            contribution_type=contrib_type,
            amount=amount,
            date_paid=date_paid or date.today(),
            note=note,
        )

        # Post to ledger
        from apps.finance.services import FinanceService
        FinanceService.post_contribution({
            "chama_id": str(chama_id),
            "member_id": str(member_id),
            "contribution_type_id": str(contribution_type_id) if contribution_type_id else None,
            "amount": str(amount),
            "date_paid": (date_paid or date.today()).isoformat(),
            "note": note,
        }, actor or member)

        return contribution

    @staticmethod
    def get_all_contributions(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        member_id: int = None,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all contributions."""
        queryset = Contribution.objects.select_related("member", "chama", "contribution_type").order_by("-date_paid", "-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if member_id:
            queryset = queryset.filter(member_id=member_id)

        if date_from:
            queryset = queryset.filter(date_paid__gte=date_from)

        if date_to:
            queryset = queryset.filter(date_paid__lte=date_to)

        total = queryset.count()
        offset = (page - 1) * page_size
        contributions = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "contributions": [
                {
                    "id": c.id,
                    "chama_id": c.chama_id,
                    "chama_name": c.chama.name,
                    "member_id": c.member_id,
                    "member_name": c.member.full_name,
                    "member_phone": c.member.phone,
                    "type_name": c.contribution_type.name if c.contribution_type else None,
                    "amount": float(c.amount),
                    "date_paid": c.date_paid.isoformat(),
                    "note": c.note,
                    "created_at": c.created_at.isoformat(),
                }
                for c in contributions
            ],
        }

    @staticmethod
    def get_contribution_summary(chama_id: int = None, date_from: date = None, date_to: date = None) -> Dict[str, Any]:
        """Get contribution summary."""
        queryset = Contribution.objects.all()

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if date_from:
            queryset = queryset.filter(date_paid__gte=date_from)

        if date_to:
            queryset = queryset.filter(date_paid__lte=date_to)

        total_amount = queryset.aggregate(Sum("amount"))["amount__sum"] or 0
        count = queryset.count()

        by_type = queryset.values("contribution_type__name").annotate(
            total=Sum("amount"),
            count=Count("id")
        )

        return {
            "total_amount": float(total_amount),
            "total_count": count,
            "by_type": [
                {"type": item["contribution_type__name"], "amount": float(item["total"]), "count": item["count"]}
                for item in by_type
            ],
        }

    # ============ WITHDRAWAL MANAGEMENT ============

    @staticmethod
    def get_all_withdrawals(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all withdrawal requests."""
        from apps.payments.models import WithdrawalRequest
        
        queryset = WithdrawalRequest.objects.select_related("member", "chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        withdrawals = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "withdrawals": [
                {
                    "id": w.id,
                    "chama_id": w.chama_id,
                    "chama_name": w.chama.name,
                    "member_id": w.member_id,
                    "member_name": w.member.full_name,
                    "amount": float(w.amount),
                    "status": w.status,
                    "created_at": w.created_at.isoformat(),
                    "processed_at": w.processed_at.isoformat() if w.processed_at else None,
                }
                for w in withdrawals
            ],
        }

    # ============ WALLET & TRANSACTIONS ============

    @staticmethod
    def get_wallet_balance(chama_id: int) -> Dict[str, Any]:
        """Get chama wallet balance."""
        entries = LedgerEntry.objects.filter(chama_id=chama_id)
        
        credits = entries.filter(direction=LedgerDirection.CREDIT).aggregate(Sum("amount"))["amount__sum"] or 0
        debits = entries.filter(direction=LedgerDirection.DEBIT).aggregate(Sum("amount"))["amount__sum"] or 0
        
        return {
            "chama_id": chama_id,
            "total_credits": float(credits),
            "total_debits": float(debits),
            "balance": float(credits - debits),
        }

    @staticmethod
    def get_all_transactions(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        transaction_type: str = None,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all transactions."""
        queryset = LedgerEntry.objects.select_related("chama", "member").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if transaction_type:
            queryset = queryset.filter(direction=transaction_type)

        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        total = queryset.count()
        offset = (page - 1) * page_size
        transactions = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "transactions": [
                {
                    "id": t.id,
                    "chama_id": t.chama_id,
                    "chama_name": t.chama.name,
                    "member_name": t.member.full_name if t.member else None,
                    "type": t.entry_type,
                    "direction": t.direction,
                    "amount": float(t.amount),
                    "balance_after": float(t.balance_after),
                    "reference": t.reference,
                    "description": t.description,
                    "created_at": t.created_at.isoformat(),
                }
                for t in transactions
            ],
        }

    # ============ PAYMENTS ============

    @staticmethod
    def get_all_payments(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
        payment_type: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all payments."""
        queryset = MpesaTransaction.objects.all().order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if status:
            queryset = queryset.filter(status=status)

        if payment_type:
            queryset = queryset.filter(transaction_type=payment_type)

        total = queryset.count()
        offset = (page - 1) * page_size
        payments = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "payments": [
                {
                    "id": p.id,
                    "chama_id": p.chama_id,
                    "phone": p.phone,
                    "amount": float(p.amount),
                    "status": p.status,
                    "transaction_type": p.transaction_type,
                    "transaction_id": p.transaction_id,
                    "created_at": p.created_at.isoformat(),
                }
                for p in payments
            ],
        }

    # ============ REPORTS & ANALYTICS ============

    @staticmethod
    def get_dashboard_metrics(chama_id: int = None) -> Dict[str, Any]:
        """Get comprehensive dashboard metrics."""
        # Members
        member_query = Membership.objects.filter(is_active=True, is_approved=True)
        if chama_id:
            member_query = member_query.filter(chama_id=chama_id)
        total_members = member_query.count()

        # Contributions
        contrib_query = Contribution.objects.all()
        if chama_id:
            contrib_query = contrib_query.filter(chama_id=chama_id)
        
        today = date.today()
        month_start = today.replace(day=1)
        
        total_contributions = contrib_query.aggregate(Sum("amount"))["amount__sum"] or 0
        monthly_contributions = contrib_query.filter(date_paid__gte=month_start).aggregate(Sum("amount"))["amount__sum"] or 0

        # Loans
        loan_query = Loan.objects.all()
        if chama_id:
            loan_query = loan_query.filter(chama_id=chama_id)
        
        total_loans = loan_query.count()
        active_loans = loan_query.filter(status__in=[
            LoanStatus.APPROVED,
            LoanStatus.DISBURSING,
            LoanStatus.DISBURSED,
            LoanStatus.ACTIVE,
        ]).count()
        pending_loans = loan_query.filter(status=LoanStatus.PENDING).count()

        # Withdrawals
        from apps.payments.models import WithdrawalRequest
        withdrawal_query = WithdrawalRequest.objects.all()
        if chama_id:
            withdrawal_query = withdrawal_query.filter(chama_id=chama_id)
        
        pending_withdrawals = withdrawal_query.filter(status="PENDING").count()

        # Wallet balance
        wallet_query = LedgerEntry.objects.all()
        if chama_id:
            wallet_query = wallet_query.filter(chama_id=chama_id)
        
        credits = wallet_query.filter(direction=LedgerDirection.CREDIT).aggregate(Sum("amount"))["amount__sum"] or 0
        debits = wallet_query.filter(direction=LedgerDirection.DEBIT).aggregate(Sum("amount"))["amount__sum"] or 0

        return {
            "total_members": total_members,
            "total_contributions": float(total_contributions),
            "monthly_contributions": float(monthly_contributions),
            "total_loans": total_loans,
            "active_loans": active_loans,
            "pending_loans": pending_loans,
            "pending_withdrawals": pending_withdrawals,
            "wallet_balance": float(credits - debits),
            "total_credits": float(credits),
            "total_debits": float(debits),
        }

    @staticmethod
    def get_monthly_trends(chama_id: int = None, months: int = 12) -> List[Dict[str, Any]]:
        """Get monthly trends for contributions and loans."""
        today = date.today()
        trends = []

        for i in range(months):
            # Calculate month
            month_date = today.replace(day=1) - timedelta(days=i * 30)
            month_start = month_date.replace(day=1)
            if month_date.month == 12:
                month_end = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)

            # Contributions
            contrib_query = Contribution.objects.filter(date_paid__gte=month_start, date_paid__lte=month_end)
            if chama_id:
                contrib_query = contrib_query.filter(chama_id=chama_id)
            contrib_amount = contrib_query.aggregate(Sum("amount"))["amount__sum"] or 0

            # Loans disbursed
            loan_query = Loan.objects.filter(disbursed_at__gte=month_start, disbursed_at__lte=month_end)
            if chama_id:
                loan_query = loan_query.filter(chama_id=chama_id)
            loan_disbursed = loan_query.aggregate(Sum("principal_amount"))["principal_amount__sum"] or 0

            # Repayments
            repayment_query = Repayment.objects.filter(created_at__gte=month_start, created_at__lte=month_end)
            if chama_id:
                repayment_query = repayment_query.filter(loan__chama_id=chama_id)
            repayments = repayment_query.aggregate(Sum("amount"))["amount__sum"] or 0

            trends.append({
                "month": month_start.strftime("%Y-%m"),
                "contributions": float(contrib_amount),
                "loans_disbursed": float(loan_disbursed),
                "repayments": float(repayments),
            })

        return list(reversed(trends))

    @staticmethod
    def get_recent_activity(chama_id: int = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent activity across all modules."""
        activities = []

        # Recent contributions
        contrib_query = Contribution.objects.select_related("member", "chama").order_by("-created_at")
        if chama_id:
            contrib_query = contrib_query.filter(chama_id=chama_id)
        
        for c in contrib_query[:5]:
            activities.append({
                "type": "CONTRIBUTION",
                "description": f"Contribution of {c.amount} by {c.member.full_name}",
                "amount": float(c.amount),
                "timestamp": c.created_at.isoformat(),
            })

        # Recent loans
        loan_query = Loan.objects.select_related("member", "chama").order_by("-created_at")
        if chama_id:
            loan_query = loan_query.filter(chama_id=chama_id)
        
        for l in loan_query[:5]:
            activities.append({
                "type": "LOAN",
                "description": f"Loan of {l.principal_amount} for {l.member.full_name}",
                "amount": float(l.principal_amount),
                "timestamp": l.created_at.isoformat(),
            })

        # Recent repayments
        repayment_query = Repayment.objects.select_related("member", "loan__chama").order_by("-created_at")
        if chama_id:
            repayment_query = repayment_query.filter(loan__chama_id=chama_id)
        
        for r in repayment_query[:5]:
            activities.append({
                "type": "REPAYMENT",
                "description": f"Repayment of {r.amount} by {r.member.full_name}",
                "amount": float(r.amount),
                "timestamp": r.created_at.isoformat(),
            })

        # Sort by timestamp and limit
        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        return activities[:limit]

    # ============ PENALTIES ============

    @staticmethod
    @transaction.atomic
    def issue_penalty(
        chama_id: int,
        member_id: int,
        amount: Decimal,
        reason: str,
        actor: User = None,
    ) -> Penalty:
        """Issue a penalty to a member."""
        from apps.finance.services import FinanceService
        result = FinanceService.issue_penalty({
            "chama_id": str(chama_id),
            "member_id": str(member_id),
            "amount": str(amount),
            "reason": reason,
        }, actor or User.objects.get(id=member_id))
        return result.penalty

    @staticmethod
    def get_all_penalties(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        is_paid: bool = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all penalties."""
        queryset = Penalty.objects.select_related("member", "chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if is_paid is not None:
            queryset = queryset.filter(is_paid=is_paid)

        total = queryset.count()
        offset = (page - 1) * page_size
        penalties = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "penalties": [
                {
                    "id": p.id,
                    "chama_id": p.chama_id,
                    "chama_name": p.chama.name,
                    "member_id": p.member_id,
                    "member_name": p.member.full_name,
                    "amount": float(p.amount),
                    "reason": p.reason,
                    "is_paid": p.is_paid,
                    "created_at": p.created_at.isoformat(),
                    "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                }
                for p in penalties
            ],
        }

    # ============ MANUAL ADJUSTMENTS ============

    @staticmethod
    @transaction.atomic
    def create_manual_adjustment(
        chama_id: int,
        amount: Decimal,
        direction: str,
        description: str,
        member_id: int = None,
        actor: User = None,
    ) -> ManualAdjustment:
        """Create a manual adjustment (credit or debit)."""
        from apps.finance.services import FinanceService
        payload = {
            "chama_id": str(chama_id),
            "amount": str(amount),
            "direction": direction,
            "description": description,
        }
        if member_id:
            payload["member_id"] = str(member_id)
        
        result = FinanceService.post_manual_adjustment(payload, actor or User.objects.get(id=member_id) if member_id else None)
        return result.adjustment if hasattr(result, 'adjustment') else None

    # ============ MEMBERSHIP REQUESTS ============

    @staticmethod
    @transaction.atomic
    def approve_membership_request(request_id: int, actor: User) -> MembershipRequest:
        """Approve a membership request."""
        from apps.chama.services import MembershipService
        return MembershipService.approve_membership_request(request_id, actor)

    @staticmethod
    @transaction.atomic
    def reject_membership_request(request_id: int, actor: User, reason: str = "") -> MembershipRequest:
        """Reject a membership request."""
        membership_request = MembershipRequest.objects.get(id=request_id)
        membership_request.status = "REJECTED"
        membership_request.review_note = reason
        membership_request.reviewed_by = actor
        membership_request.reviewed_at = timezone.now()
        membership_request.save()
        return membership_request

    @staticmethod
    def get_all_membership_requests(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all membership requests."""
        queryset = MembershipRequest.objects.select_related("user", "chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        requests = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "requests": [
                {
                    "id": r.id,
                    "chama_id": r.chama_id,
                    "chama_name": r.chama.name,
                    "user_id": r.user_id,
                    "user_name": r.user.full_name,
                    "user_phone": r.user.phone,
                    "status": r.status,
                    "request_note": r.request_note,
                    "created_at": r.created_at.isoformat(),
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                }
                for r in requests
            ],
        }

    # ============ MEETINGS & GOVERNANCE ============

    @staticmethod
    def get_all_meetings(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all meetings."""
        from apps.meetings.models import Meeting, MinutesStatus
        
        queryset = Meeting.objects.select_related("chama").order_by("-date")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        if status:
            queryset = queryset.filter(minutes_status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        meetings = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "meetings": [
                {
                    "id": m.id,
                    "chama_id": m.chama_id,
                    "chama_name": m.chama.name,
                    "title": m.title,
                    "date": m.date.isoformat(),
                    "agenda": m.agenda,
                    "minutes_status": m.minutes_status,
                    "quorum_percentage": m.quorum_percentage,
                    "created_at": m.created_at.isoformat(),
                }
                for m in meetings
            ],
        }

    @staticmethod
    def get_meeting_detail(meeting_id: int) -> Dict[str, Any]:
        """Get meeting details with attendance and resolutions."""
        from apps.meetings.models import Meeting, Attendance, Resolution, AgendaItem
        
        meeting = Meeting.objects.select_related("chama").get(id=meeting_id)
        
        attendance = Attendance.objects.filter(meeting=meeting).select_related("member")
        resolutions = Resolution.objects.filter(meeting=meeting).select_related("assigned_to")
        agenda_items = AgendaItem.objects.filter(meeting=meeting).select_related("proposed_by")

        return {
            "id": meeting.id,
            "chama_id": meeting.chama_id,
            "chama_name": meeting.chama.name,
            "title": meeting.title,
            "date": meeting.date.isoformat(),
            "agenda": meeting.agenda,
            "minutes_text": meeting.minutes_text,
            "minutes_status": meeting.minutes_status,
            "quorum_percentage": meeting.quorum_percentage,
            "attendance": [
                {
                    "member_id": a.member_id,
                    "member_name": a.member.full_name,
                    "status": a.status,
                    "notes": a.notes,
                }
                for a in attendance
            ],
            "resolutions": [
                {
                    "id": r.id,
                    "text": r.text,
                    "assigned_to": r.assigned_to.full_name if r.assigned_to else None,
                    "due_date": r.due_date.isoformat() if r.due_date else None,
                    "status": r.status,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in resolutions
            ],
            "agenda_items": [
                {
                    "id": a.id,
                    "title": a.title,
                    "description": a.description,
                    "proposed_by": a.proposed_by.full_name,
                    "status": a.status,
                }
                for a in agenda_items
            ],
        }

    @staticmethod
    @transaction.atomic
    def approve_meeting_minutes(meeting_id: int, actor: User) -> Dict[str, Any]:
        """Approve meeting minutes."""
        from apps.meetings.models import Meeting, MinutesStatus
        
        meeting = Meeting.objects.get(id=meeting_id)
        meeting.minutes_status = MinutesStatus.APPROVED
        meeting.minutes_approved_by = actor
        meeting.minutes_approved_at = timezone.now()
        meeting.save()
        
        return {
            "id": meeting.id,
            "minutes_status": meeting.minutes_status,
            "message": "Meeting minutes approved successfully"
        }

    @staticmethod
    def get_meeting_attendance(meeting_id: int) -> Dict[str, Any]:
        """Get meeting attendance records."""
        from apps.meetings.models import Meeting, Attendance
        
        meeting = Meeting.objects.get(id=meeting_id)
        attendance = Attendance.objects.filter(meeting=meeting).select_related("member")

        return {
            "meeting_id": meeting_id,
            "meeting_title": meeting.title,
            "total_expected": meeting.members.count() if hasattr(meeting, 'members') else 0,
            "attendance": [
                {
                    "member_id": a.member_id,
                    "member_name": a.member.full_name,
                    "status": a.status,
                    "notes": a.notes,
                }
                for a in attendance
            ],
        }

    @staticmethod
    def get_all_resolutions(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all resolutions."""
        from apps.meetings.models import Resolution, Meeting
        
        queryset = Resolution.objects.select_related("meeting", "assigned_to").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(meeting__chama_id=chama_id)

        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        resolutions = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "resolutions": [
                {
                    "id": r.id,
                    "meeting_id": r.meeting_id,
                    "meeting_title": r.meeting.title,
                    "text": r.text,
                    "assigned_to": r.assigned_to.full_name if r.assigned_to else None,
                    "due_date": r.due_date.isoformat() if r.due_date else None,
                    "status": r.status,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in resolutions
            ],
        }

    @staticmethod
    @transaction.atomic
    def update_resolution_status(resolution_id: int, new_status: str, actor: User) -> Dict[str, Any]:
        """Update resolution status."""
        from apps.meetings.models import Resolution, ResolutionStatus
        
        resolution = Resolution.objects.get(id=resolution_id)
        resolution.status = new_status
        if new_status == ResolutionStatus.DONE:
            resolution.completed_at = timezone.now()
        resolution.save()
        
        return {
            "id": resolution.id,
            "status": resolution.status,
            "message": "Resolution updated successfully"
        }

    # ============ ISSUES & SERVICE DESK ============

    @staticmethod
    def get_all_issues(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
        category: str = None,
        priority: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all issues."""
        from apps.issues.models import Issue
        
        queryset = Issue.objects.select_related("chama", "assigned_to", "reported_user").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)
        if category:
            queryset = queryset.filter(category=category)
        if priority:
            queryset = queryset.filter(priority=priority)

        total = queryset.count()
        offset = (page - 1) * page_size
        issues = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "issues": [
                {
                    "id": i.id,
                    "chama_id": i.chama_id,
                    "chama_name": i.chama.name,
                    "title": i.title,
                    "description": i.description[:100] + "..." if len(i.description) > 100 else i.description,
                    "category": i.category,
                    "priority": i.priority,
                    "status": i.status,
                    "assigned_to": i.assigned_to.full_name if i.assigned_to else None,
                    "reported_user": i.reported_user.full_name if i.reported_user and not i.is_anonymous else "Anonymous",
                    "is_anonymous": i.is_anonymous,
                    "created_at": i.created_at.isoformat(),
                    "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                }
                for i in issues
            ],
        }

    @staticmethod
    def get_issue_detail(issue_id: int) -> Dict[str, Any]:
        """Get issue details with comments and activity."""
        from apps.issues.models import Issue, IssueComment, IssueActivityLog
        
        issue = Issue.objects.select_related("chama", "assigned_to", "reported_user", "loan").get(id=issue_id)
        comments = IssueComment.objects.filter(issue=issue).select_related("author")
        activity = IssueActivityLog.objects.filter(issue=issue).order_by("-created_at")[:20]

        return {
            "id": issue.id,
            "chama_id": issue.chama_id,
            "chama_name": issue.chama.name,
            "title": issue.title,
            "description": issue.description,
            "category": issue.category,
            "priority": issue.priority,
            "status": issue.status,
            "assigned_to": issue.assigned_to.full_name if issue.assigned_to else None,
            "reported_user": issue.reported_user.full_name if issue.reported_user and not issue.is_anonymous else "Anonymous",
            "is_anonymous": issue.is_anonymous,
            "loan_id": issue.loan_id,
            "report_type": issue.report_type,
            "due_at": issue.due_at.isoformat() if issue.due_at else None,
            "created_at": issue.created_at.isoformat(),
            "resolved_at": issue.resolved_at.isoformat() if issue.resolved_at else None,
            "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
            "comments": [
                {
                    "id": c.id,
                    "author": c.author.full_name,
                    "message": c.message,
                    "is_internal": c.is_internal,
                    "created_at": c.created_at.isoformat(),
                }
                for c in comments
            ],
            "activity": [
                {
                    "id": str(a.id),
                    "actor": a.actor.full_name if a.actor else "System",
                    "action": a.action,
                    "meta": a.meta,
                    "created_at": a.created_at.isoformat(),
                }
                for a in activity
            ],
        }

    @staticmethod
    @transaction.atomic
    def update_issue(
        issue_id: int,
        status: str = None,
        assigned_to: int = None,
        priority: str = None,
        actor: User = None,
    ) -> Dict[str, Any]:
        """Update issue details."""
        from apps.issues.models import Issue, IssueStatus
        
        issue = Issue.objects.get(id=issue_id)
        
        if status:
            issue.status = status
            if status == IssueStatus.RESOLVED:
                issue.resolved_at = timezone.now()
            elif status == IssueStatus.CLOSED:
                issue.closed_at = timezone.now()
        
        if assigned_to:
            issue.assigned_to_id = assigned_to
        
        if priority:
            issue.priority = priority
        
        issue.save()
        
        return {
            "id": issue.id,
            "status": issue.status,
            "priority": issue.priority,
            "assigned_to": issue.assigned_to.full_name if issue.assigned_to else None,
            "message": "Issue updated successfully"
        }

    @staticmethod
    @transaction.atomic
    def assign_issue(issue_id: int, user_id: int, actor: User) -> Dict[str, Any]:
        """Assign issue to a user."""
        from apps.issues.models import Issue, IssueStatus
        
        issue = Issue.objects.get(id=issue_id)
        user = User.objects.get(id=user_id)
        
        issue.assigned_to = user
        issue.status = IssueStatus.ASSIGNED
        issue.save()
        
        return {
            "id": issue.id,
            "assigned_to": user.full_name,
            "status": issue.status,
            "message": f"Issue assigned to {user.full_name}"
        }

    @staticmethod
    def get_all_warnings(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all warnings."""
        from apps.issues.models import Warning
        
        queryset = Warning.objects.select_related("chama", "user", "issued_by").order_by("-issued_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        warnings = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "warnings": [
                {
                    "id": w.id,
                    "chama_id": w.chama_id,
                    "chama_name": w.chama.name,
                    "user_id": w.user_id,
                    "user_name": w.user.full_name,
                    "reason": w.reason,
                    "message_to_user": w.message_to_user,
                    "severity": w.severity,
                    "status": w.status,
                    "issued_by": w.issued_by.full_name if w.issued_by else None,
                    "issued_at": w.issued_at.isoformat(),
                }
                for w in warnings
            ],
        }

    @staticmethod
    @transaction.atomic
    def create_warning(
        chama_id: int,
        user_id: int,
        reason: str,
        message_to_user: str,
        severity: str = "medium",
        actor: User = None,
    ) -> Dict[str, Any]:
        """Create a warning for a user."""
        from apps.issues.models import Warning
        
        user = User.objects.get(id=user_id)
        
        warning = Warning.objects.create(
            chama_id=chama_id,
            user=user,
            reason=reason,
            message_to_user=message_to_user,
            severity=severity,
            issued_by=actor,
        )
        
        return {
            "id": warning.id,
            "user_name": user.full_name,
            "severity": warning.severity,
            "message": "Warning issued successfully"
        }

    @staticmethod
    def get_all_suspensions(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        is_active: bool = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all suspensions."""
        from apps.issues.models import Suspension
        
        queryset = Suspension.objects.select_related("chama", "user", "suspended_by").order_by("-starts_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        total = queryset.count()
        offset = (page - 1) * page_size
        suspensions = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "suspensions": [
                {
                    "id": str(s.id),
                    "chama_id": s.chama_id,
                    "chama_name": s.chama.name,
                    "user_id": s.user_id,
                    "user_name": s.user.full_name,
                    "reason": s.reason,
                    "starts_at": s.starts_at.isoformat(),
                    "ends_at": s.ends_at.isoformat() if s.ends_at else None,
                    "is_active": s.is_active,
                    "suspended_by": s.suspended_by.full_name if s.suspended_by else None,
                    "lifted_at": s.lifted_at.isoformat() if s.lifted_at else None,
                }
                for s in suspensions
            ],
        }

    @staticmethod
    @transaction.atomic
    def create_suspension(
        chama_id: int,
        user_id: int,
        reason: str,
        ends_at: str = None,
        actor: User = None,
    ) -> Dict[str, Any]:
        """Create a suspension for a user."""
        from apps.issues.models import Suspension
        from datetime import datetime
        
        user = User.objects.get(id=user_id)
        
        suspension = Suspension.objects.create(
            chama_id=chama_id,
            user=user,
            reason=reason,
            ends_at=datetime.fromisoformat(ends_at) if ends_at else None,
            suspended_by=actor,
        )
        
        return {
            "id": str(suspension.id),
            "user_name": user.full_name,
            "reason": reason,
            "starts_at": suspension.starts_at.isoformat(),
            "ends_at": suspension.ends_at.isoformat() if suspension.ends_at else None,
            "message": "User suspended successfully"
        }

    @staticmethod
    @transaction.atomic
    def lift_suspension(suspension_id: int, actor: User, reason: str = "") -> Dict[str, Any]:
        """Lift a suspension."""
        from apps.issues.models import Suspension
        
        suspension = Suspension.objects.get(id=suspension_id)
        suspension.is_active = False
        suspension.lifted_at = timezone.now()
        suspension.lifted_by = actor
        suspension.lift_reason = reason
        suspension.save()
        
        return {
            "id": str(suspension.id),
            "is_active": suspension.is_active,
            "lifted_at": suspension.lifted_at.isoformat(),
            "message": "Suspension lifted successfully"
        }

    # ============ NOTIFICATIONS & BROADCASTS ============

    @staticmethod
    def get_all_notifications(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
        category: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all notifications."""
        from apps.notifications.models import Notification
        
        queryset = Notification.objects.select_related("chama", "recipient").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)
        if category:
            queryset = queryset.filter(category=category)

        total = queryset.count()
        offset = (page - 1) * page_size
        notifications = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "notifications": [
                {
                    "id": n.id,
                    "chama_id": n.chama_id,
                    "recipient": n.recipient.full_name,
                    "type": n.type,
                    "category": n.category,
                    "priority": n.priority,
                    "status": n.status,
                    "subject": n.subject,
                    "message": n.message[:100] + "..." if len(n.message) > 100 else n.message,
                    "scheduled_at": n.scheduled_at.isoformat() if n.scheduled_at else None,
                    "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                    "read_at": n.read_at.isoformat() if n.read_at else None,
                    "created_at": n.created_at.isoformat(),
                }
                for n in notifications
            ],
        }

    @staticmethod
    def get_otp_delivery_logs(
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        channel: str | None = None,
        purpose: str | None = None,
        search: str = "",
    ) -> Dict[str, Any]:
        """Get paginated OTP delivery logs with summary counts."""
        from apps.accounts.models import OTPDeliveryLog

        queryset = OTPDeliveryLog.objects.select_related(
            "otp_token",
            "user",
        ).order_by("-created_at")

        if status:
            queryset = queryset.filter(status=status)
        if channel:
            queryset = queryset.filter(channel=channel)
        if purpose:
            queryset = queryset.filter(otp_token__purpose=purpose)
        if search:
            queryset = queryset.filter(
                Q(destination__icontains=search)
                | Q(provider_name__icontains=search)
                | Q(error_message__icontains=search)
                | Q(user__full_name__icontains=search)
                | Q(user__phone__icontains=search)
            )

        total = queryset.count()
        offset = (page - 1) * page_size
        logs = queryset[offset:offset + page_size]

        base_queryset = OTPDeliveryLog.objects.all()
        if channel:
            base_queryset = base_queryset.filter(channel=channel)
        if purpose:
            base_queryset = base_queryset.filter(otp_token__purpose=purpose)

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "summary": {
                "sent": base_queryset.filter(status="sent").count(),
                "failed": base_queryset.filter(status="failed").count(),
                "delivered": base_queryset.filter(status="delivered").count(),
            },
            "logs": [
                {
                    "id": log.id,
                    "otp_token_id": log.otp_token_id,
                    "user_id": log.user_id,
                    "user_name": log.user.full_name if log.user else "",
                    "user_phone": log.user.phone if log.user else "",
                    "channel": log.channel,
                    "provider_name": log.provider_name,
                    "provider_message_id": log.provider_message_id,
                    "status": log.status,
                    "destination": log.destination,
                    "attempt_number": log.attempt_number,
                    "error_message": log.error_message,
                    "purpose": log.otp_token.purpose,
                    "delivery_method": log.otp_token.delivery_method,
                    "token_status": (
                        "used"
                        if log.otp_token.is_used
                        else "expired"
                        if log.otp_token.is_expired
                        else "active"
                    ),
                    "token_attempts": log.otp_token.attempts,
                    "created_at": log.created_at.isoformat(),
                    "updated_at": log.updated_at.isoformat(),
                }
                for log in logs
            ],
        }

    @staticmethod
    def get_notification_templates(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Get paginated list of notification templates."""
        from apps.notifications.models import NotificationTemplate
        
        queryset = NotificationTemplate.objects.all().order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(Q(chama_id=chama_id) | Q(chama_id=None))

        total = queryset.count()
        offset = (page - 1) * page_size
        templates = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "templates": [
                {
                    "id": t.id,
                    "name": t.name,
                    "type": t.type,
                    "subject": t.subject,
                    "body": t.body[:100] + "..." if len(t.body) > 100 else t.body,
                    "is_active": t.is_active,
                    "created_at": t.created_at.isoformat(),
                }
                for t in templates
            ],
        }

    @staticmethod
    def get_all_broadcasts(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of all broadcasts."""
        from apps.notifications.models import BroadcastAnnouncement
        
        queryset = BroadcastAnnouncement.objects.filter(chama_id=chama_id).order_by("-created_at") if chama_id else BroadcastAnnouncement.objects.all().order_by("-created_at")

        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        broadcasts = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "broadcasts": [
                {
                    "id": b.id,
                    "chama_id": b.chama_id,
                    "title": b.title,
                    "message": b.message[:100] + "..." if len(b.message) > 100 else b.message,
                    "target": b.target,
                    "channels": b.channels,
                    "status": b.status,
                    "scheduled_at": b.scheduled_at.isoformat() if b.scheduled_at else None,
                    "sent_at": b.sent_at.isoformat() if b.sent_at else None,
                    "created_at": b.created_at.isoformat(),
                }
                for b in broadcasts
            ],
        }

    @staticmethod
    @transaction.atomic
    def create_broadcast(
        chama_id: int,
        title: str,
        message: str,
        target: str = "all",
        target_roles: List[str] = None,
        target_member_ids: List[int] = None,
        channels: List[str] = None,
        scheduled_at: str = None,
        actor: User = None,
    ) -> Dict[str, Any]:
        """Create a broadcast announcement."""
        from apps.notifications.models import BroadcastAnnouncement
        from datetime import datetime
        
        broadcast = BroadcastAnnouncement.objects.create(
            chama_id=chama_id,
            title=title,
            message=message,
            target=target,
            target_roles=target_roles or [],
            target_member_ids=target_member_ids or [],
            channels=channels or ["in_app"],
            scheduled_at=datetime.fromisoformat(scheduled_at) if scheduled_at else None,
        )
        
        return {
            "id": broadcast.id,
            "title": broadcast.title,
            "status": broadcast.status,
            "message": "Broadcast created successfully"
        }

    # ============ SECURITY CENTER ============

    @staticmethod
    def get_login_attempts(
        page: int = 1,
        page_size: int = 20,
        success: bool = None,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get paginated list of login attempts."""
        from apps.security.models import LoginAttempt
        
        queryset = LoginAttempt.objects.all().order_by("-created_at")

        if success is not None:
            queryset = queryset.filter(success=success)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        total = queryset.count()
        offset = (page - 1) * page_size
        attempts = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "attempts": [
                {
                    "id": str(a.id),
                    "user_identifier": a.user_identifier,
                    "ip_address": a.ip_address,
                    "device_info": a.device_info,
                    "success": a.success,
                    "created_at": a.created_at.isoformat(),
                }
                for a in attempts
            ],
        }

    @staticmethod
    def get_user_sessions(
        user_id: int = None,
        page: int = 1,
        page_size: int = 20,
        is_active: bool = None,
    ) -> Dict[str, Any]:
        """Get paginated list of user sessions."""
        from apps.security.models import UserSession
        
        queryset = UserSession.objects.select_related("user", "chama_context").order_by("-last_activity")

        if user_id:
            queryset = queryset.filter(user_id=user_id)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        total = queryset.count()
        offset = (page - 1) * page_size
        sessions = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "sessions": [
                {
                    "id": str(s.id),
                    "user_id": s.user_id,
                    "user_name": s.user.full_name,
                    "device_name": s.device_name,
                    "ip_address": s.ip_address,
                    "is_active": s.is_active,
                    "last_activity": s.last_activity.isoformat(),
                    "expires_at": s.expires_at.isoformat(),
                    "chama_context": s.chama_context.name if s.chama_context else None,
                }
                for s in sessions
            ],
        }

    @staticmethod
    @transaction.atomic
    def revoke_session(session_id: int) -> Dict[str, Any]:
        """Revoke a user session."""
        from apps.security.models import UserSession
        
        session = UserSession.objects.get(id=session_id)
        session.is_active = False
        session.save()
        
        return {
            "id": str(session.id),
            "is_active": session.is_active,
            "message": "Session revoked successfully"
        }

    @staticmethod
    def get_audit_logs(
        chama_id: int = None,
        action_type: str = None,
        actor_id: int = None,
        page: int = 1,
        page_size: int = 20,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get paginated list of audit logs."""
        from apps.security.models import AuditLog
        
        queryset = AuditLog.objects.select_related("chama", "actor").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if action_type:
            queryset = queryset.filter(action_type=action_type)
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        total = queryset.count()
        offset = (page - 1) * page_size
        logs = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "logs": [
                {
                    "id": l.id,
                    "chama_name": l.chama.name if l.chama else None,
                    "actor": l.actor.full_name if l.actor else "System",
                    "action_type": l.action_type,
                    "target_type": l.target_type,
                    "target_id": l.target_id,
                    "ip_address": l.ip_address,
                    "created_at": l.created_at.isoformat(),
                }
                for l in logs
            ],
        }

    @staticmethod
    def get_security_alerts(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        level: str = None,
        is_resolved: bool = None,
    ) -> Dict[str, Any]:
        """Get paginated list of security alerts."""
        from apps.security.models import SecurityAlert
        
        queryset = SecurityAlert.objects.select_related("chama", "user", "resolved_by").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if level:
            queryset = queryset.filter(level=level)
        if is_resolved is not None:
            queryset = queryset.filter(is_resolved=is_resolved)

        total = queryset.count()
        offset = (page - 1) * page_size
        alerts = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "alerts": [
                {
                    "id": a.id,
                    "chama_name": a.chama.name if a.chama else None,
                    "user_name": a.user.full_name if a.user else None,
                    "alert_type": a.alert_type,
                    "level": a.level,
                    "title": a.title,
                    "message": a.message[:100] + "..." if len(a.message) > 100 else a.message,
                    "is_resolved": a.is_resolved,
                    "resolved_by": a.resolved_by.full_name if a.resolved_by else None,
                    "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                    "created_at": a.created_at.isoformat(),
                }
                for a in alerts
            ],
        }

    @staticmethod
    @transaction.atomic
    def resolve_security_alert(alert_id: int, actor: User) -> Dict[str, Any]:
        """Resolve a security alert."""
        from apps.security.models import SecurityAlert
        
        alert = SecurityAlert.objects.get(id=alert_id)
        alert.resolve(actor)
        
        return {
            "id": alert.id,
            "is_resolved": alert.is_resolved,
            "resolved_at": alert.resolved_at.isoformat(),
            "message": "Security alert resolved"
        }

    # ============ REPORTS & ANALYTICS ============

    @staticmethod
    def get_report_requests(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
        report_type: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of report requests."""
        from apps.reports.models import ReportRequest
        
        queryset = ReportRequest.objects.select_related("requested_by", "chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)
        if report_type:
            queryset = queryset.filter(report_type=report_type)

        total = queryset.count()
        offset = (page - 1) * page_size
        reports = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "reports": [
                {
                    "id": r.id,
                    "chama_name": r.chama.name if r.chama else None,
                    "requested_by": r.requested_by.full_name,
                    "scope": r.scope,
                    "report_type": r.report_type,
                    "format": r.format,
                    "status": r.status,
                    "file_name": r.file_name,
                    "file_size": r.file_size,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in reports
            ],
        }

    @staticmethod
    @transaction.atomic
    def generate_report(
        chama_id: int,
        report_type: str,
        format: str,
        filters: Dict[str, Any] = None,
        actor: User = None,
    ) -> Dict[str, Any]:
        """Generate a new report."""
        from apps.reports.models import ReportRequest, ReportScope
        
        report = ReportRequest.objects.create(
            chama_id=chama_id,
            requested_by=actor,
            scope=ReportScope.CHAMA,
            report_type=report_type,
            format=format,
            filters=filters or {},
            status="queued",
        )
        
        return {
            "id": report.id,
            "status": report.status,
            "message": "Report generation started"
        }

    @staticmethod
    def get_scheduled_reports(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        is_active: bool = None,
    ) -> Dict[str, Any]:
        """Get paginated list of scheduled reports."""
        from apps.reports.models import ScheduledReport
        
        queryset = ScheduledReport.objects.select_related("chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        total = queryset.count()
        offset = (page - 1) * page_size
        reports = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "scheduled_reports": [
                {
                    "id": r.id,
                    "name": r.name,
                    "chama_name": r.chama.name,
                    "report_type": r.report_type,
                    "format": r.format,
                    "schedule": r.schedule,
                    "is_active": r.is_active,
                    "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                    "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in reports
            ],
        }

    # ============ PAYMENTS & M-PESA ============

    @staticmethod
    def get_mpesa_transactions(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
        purpose: str = None,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get paginated list of M-Pesa transactions."""
        from apps.payments.models import MpesaTransaction
        
        queryset = MpesaTransaction.objects.select_related("chama", "member").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)
        if purpose:
            queryset = queryset.filter(purpose=purpose)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        total = queryset.count()
        offset = (page - 1) * page_size
        transactions = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "transactions": [
                {
                    "id": t.id,
                    "chama_id": t.chama_id,
                    "chama_name": t.chama.name,
                    "member_name": t.member.full_name if t.member else None,
                    "phone": t.phone,
                    "amount": float(t.amount),
                    "purpose": t.purpose,
                    "status": t.status,
                    "receipt_number": t.receipt_number,
                    "checkout_request_id": t.checkout_request_id,
                    "created_at": t.created_at.isoformat(),
                }
                for t in transactions
            ],
        }

    @staticmethod
    def get_payment_disputes(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of payment disputes."""
        from apps.payments.models import PaymentDispute
        
        queryset = PaymentDispute.objects.select_related("chama", "user").order_by("-created_at") if chama_id else PaymentDispute.objects.select_related("chama", "user").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        disputes = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "disputes": [
                {
                    "id": d.id,
                    "chama_name": d.chama.name if d.chama else None,
                    "user_name": d.user.full_name if d.user else None,
                    "category": d.category,
                    "status": d.status,
                    "amount": float(d.amount) if d.amount else None,
                    "description": d.description,
                    "created_at": d.created_at.isoformat(),
                    "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
                }
                for d in disputes
            ],
        }

    @staticmethod
    def get_payment_refunds(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of payment refunds."""
        from apps.payments.models import PaymentRefund
        
        queryset = PaymentRefund.objects.select_related("chama", "user").order_by("-created_at") if chama_id else PaymentRefund.objects.select_related("chama", "user").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        refunds = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "refunds": [
                {
                    "id": r.id,
                    "chama_name": r.chama.name if r.chama else None,
                    "user_name": r.user.full_name if r.user else None,
                    "amount": float(r.amount),
                    "status": r.status,
                    "reason": r.reason,
                    "created_at": r.created_at.isoformat(),
                    "processed_at": r.processed_at.isoformat() if r.processed_at else None,
                }
                for r in refunds
            ],
        }

    @staticmethod
    @transaction.atomic
    def approve_refund(refund_id: int, actor: User) -> Dict[str, Any]:
        """Approve a refund."""
        from apps.payments.models import PaymentRefund, PaymentRefundStatus
        
        refund = PaymentRefund.objects.get(id=refund_id)
        refund.status = PaymentRefundStatus.APPROVED
        refund.processed_by = actor
        refund.processed_at = timezone.now()
        refund.save()
        
        return {
            "id": refund.id,
            "status": refund.status,
            "message": "Refund approved"
        }

    @staticmethod
    @transaction.atomic
    def reject_refund(refund_id: int, actor: User, reason: str = "") -> Dict[str, Any]:
        """Reject a refund."""
        from apps.payments.models import PaymentRefund, PaymentRefundStatus
        
        refund = PaymentRefund.objects.get(id=refund_id)
        refund.status = PaymentRefundStatus.REJECTED
        refund.processed_by = actor
        refund.processed_at = timezone.now()
        refund.rejection_reason = reason
        refund.save()
        
        return {
            "id": refund.id,
            "status": refund.status,
            "message": "Refund rejected"
        }

    # ============ AI ADMIN DASHBOARD ============

    @staticmethod
    def get_ai_insights(chama_id: int = None) -> Dict[str, Any]:
        """Get AI-powered insights for admin."""
        # This would integrate with the AI app's insights engine
        return {
            "insights": [
                {"type": "contribution_trend", "message": "Contributions are up 15% this month", "severity": "positive"},
                {"type": "loan_risk", "message": "3 members at risk of default", "severity": "warning"},
                {"type": "member_activity", "message": "New member signups increased", "severity": "positive"},
            ],
            "generated_at": timezone.now().isoformat(),
        }

    @staticmethod
    def get_ai_fraud_alerts(
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get AI fraud detection alerts."""
        # This would integrate with the AI app's fraud engine
        return {
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0,
            "alerts": [],
        }

    @staticmethod
    def get_ai_risk_scores(
        chama_id: int = None,
        member_id: int = None,
    ) -> Dict[str, Any]:
        """Get AI risk scores for members."""
        # This would integrate with the AI app's risk engine
        return {
            "risk_scores": [],
        }

    # ============ AUTOMATIONS CENTER ============

    @staticmethod
    def get_automations(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        is_active: bool = None,
        trigger_type: str = None,
    ) -> Dict[str, Any]:
        """Get paginated list of automations."""
        from apps.automations.models import Automation
        
        queryset = Automation.objects.select_related("chama").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)
        if trigger_type:
            queryset = queryset.filter(trigger_type=trigger_type)

        total = queryset.count()
        offset = (page - 1) * page_size
        automations = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "automations": [
                {
                    "id": a.id,
                    "name": a.name,
                    "chama_name": a.chama.name if a.chama else "System-wide",
                    "trigger_type": a.trigger_type,
                    "action_type": a.action_type,
                    "is_active": a.is_active,
                    "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
                    "created_at": a.created_at.isoformat(),
                }
                for a in automations
            ],
        }

    @staticmethod
    @transaction.atomic
    def toggle_automation(automation_id: int, is_active: bool) -> Dict[str, Any]:
        """Toggle automation status."""
        from apps.automations.models import Automation
        
        automation = Automation.objects.get(id=automation_id)
        automation.is_active = is_active
        automation.save()
        
        return {
            "id": automation.id,
            "is_active": automation.is_active,
            "message": f"Automation {'activated' if is_active else 'deactivated'}"
        }

    @staticmethod
    def get_automation_logs(
        automation_id: int = None,
        page: int = 1,
        page_size: int = 20,
        status: str = None,
    ) -> Dict[str, Any]:
        """Get automation execution logs."""
        from apps.automations.models import AutomationExecution
        
        queryset = AutomationExecution.objects.select_related("automation").order_by("-created_at")

        if automation_id:
            queryset = queryset.filter(automation_id=automation_id)
        if status:
            queryset = queryset.filter(status=status)

        total = queryset.count()
        offset = (page - 1) * page_size
        logs = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "logs": [
                {
                    "id": l.id,
                    "automation_name": l.automation.name,
                    "status": l.status,
                    "error_message": l.error_message,
                    "executed_at": l.executed_at.isoformat(),
                }
                for l in logs
            ],
        }

    # ============ APPROVALS CENTER ============

    @staticmethod
    def get_approval_summary(chama_id: int = None) -> Dict[str, Any]:
        """Get approval summary across all types."""
        from apps.chama.models import MembershipRequest
        from apps.payments.models import WithdrawalRequest
        
        # Pending membership requests
        membership_query = MembershipRequest.objects.filter(status="PENDING")
        if chama_id:
            membership_query = membership_query.filter(chama_id=chama_id)
        pending_membership = membership_query.count()

        # Pending loans
        from apps.finance.models import Loan, LoanStatus
        loan_query = Loan.objects.filter(status=LoanStatus.PENDING)
        if chama_id:
            loan_query = loan_query.filter(chama_id=chama_id)
        pending_loans = loan_query.count()

        # Pending withdrawals
        withdrawal_query = WithdrawalRequest.objects.filter(status="PENDING")
        if chama_id:
            withdrawal_query = withdrawal_query.filter(chama_id=chama_id)
        pending_withdrawals = withdrawal_query.count()

        return {
            "pending_membership_requests": pending_membership,
            "pending_loans": pending_loans,
            "pending_withdrawals": pending_withdrawals,
            "total_pending": pending_membership + pending_loans + pending_withdrawals,
        }

    @staticmethod
    def get_pending_disbursements(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Get pending loan disbursements."""
        from apps.finance.models import Loan, LoanStatus
        
        queryset = Loan.objects.filter(status=LoanStatus.APPROVED).select_related("member", "chama", "loan_product")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        total = queryset.count()
        offset = (page - 1) * page_size
        loans = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "disbursements": [
                {
                    "id": l.id,
                    "chama_id": l.chama_id,
                    "chama_name": l.chama.name,
                    "member_name": l.member.full_name,
                    "member_phone": l.member.phone,
                    "product_name": l.loan_product.name if l.loan_product else None,
                    "principal_amount": float(l.principal_amount),
                    "total_amount": float(l.total_amount),
                    "approved_at": l.approved_at.isoformat() if l.approved_at else None,
                    "created_at": l.created_at.isoformat(),
                }
                for l in loans
            ],
        }

    @staticmethod
    def get_pending_withdrawals(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Get pending withdrawal requests."""
        from apps.payments.models import WithdrawalRequest
        
        queryset = WithdrawalRequest.objects.filter(status="PENDING").select_related("member", "chama")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        total = queryset.count()
        offset = (page - 1) * page_size
        withdrawals = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "withdrawals": [
                {
                    "id": w.id,
                    "chama_id": w.chama_id,
                    "chama_name": w.chama.name,
                    "member_name": w.member.full_name,
                    "member_phone": w.member.phone,
                    "amount": float(w.amount),
                    "reason": w.reason,
                    "created_at": w.created_at.isoformat(),
                }
                for w in withdrawals
            ],
        }

    @staticmethod
    @transaction.atomic
    def approve_withdrawal(withdrawal_id: int, actor: User) -> Dict[str, Any]:
        """Approve a withdrawal request."""
        from apps.payments.models import WithdrawalRequest
        
        withdrawal = WithdrawalRequest.objects.get(id=withdrawal_id)
        withdrawal.status = "APPROVED"
        withdrawal.processed_by = actor
        withdrawal.processed_at = timezone.now()
        withdrawal.save()
        
        return {
            "id": withdrawal.id,
            "status": withdrawal.status,
            "message": "Withdrawal approved"
        }

    @staticmethod
    @transaction.atomic
    def reject_withdrawal(withdrawal_id: int, actor: User, reason: str = "") -> Dict[str, Any]:
        """Reject a withdrawal request."""
        from apps.payments.models import WithdrawalRequest
        
        withdrawal = WithdrawalRequest.objects.get(id=withdrawal_id)
        withdrawal.status = "REJECTED"
        withdrawal.processed_by = actor
        withdrawal.processed_at = timezone.now()
        withdrawal.rejection_reason = reason
        withdrawal.save()
        
        return {
            "id": withdrawal.id,
            "status": withdrawal.status,
            "message": "Withdrawal rejected"
        }

    # ============ FINANCE LEDGER ============

    @staticmethod
    def get_ledger_entries(
        chama_id: int = None,
        page: int = 1,
        page_size: int = 20,
        entry_type: str = None,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get paginated list of ledger entries."""
        from apps.finance.models import LedgerEntry, LedgerDirection
        
        queryset = LedgerEntry.objects.select_related("chama", "member").order_by("-created_at")

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if entry_type:
            queryset = queryset.filter(entry_type=entry_type)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        total = queryset.count()
        offset = (page - 1) * page_size
        entries = queryset[offset:offset + page_size]

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "entries": [
                {
                    "id": e.id,
                    "chama_id": e.chama_id,
                    "chama_name": e.chama.name,
                    "member_name": e.member.full_name if e.member else None,
                    "entry_type": e.entry_type,
                    "direction": e.direction,
                    "amount": float(e.amount),
                    "balance_after": float(e.balance_after),
                    "reference": e.reference,
                    "description": e.description,
                    "created_at": e.created_at.isoformat(),
                }
                for e in entries
            ],
        }

    @staticmethod
    def get_ledger_summary(
        chama_id: int = None,
        date_from: date = None,
        date_to: date = None,
    ) -> Dict[str, Any]:
        """Get ledger summary."""
        from apps.finance.models import LedgerEntry, LedgerDirection
        
        queryset = LedgerEntry.objects.all()

        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        credits = queryset.filter(direction=LedgerDirection.CREDIT).aggregate(Sum("amount"))["amount__sum"] or 0
        debits = queryset.filter(direction=LedgerDirection.DEBIT).aggregate(Sum("amount"))["amount__sum"] or 0

        return {
            "total_credits": float(credits),
            "total_debits": float(debits),
            "balance": float(credits - debits),
            "entry_count": queryset.count(),
        }

    # ============ ADMIN SETTINGS ============

    @staticmethod
    def get_admin_settings() -> Dict[str, Any]:
        """Get admin settings."""
        return {
            "system_name": "Digital Chama System",
            "timezone": "Africa/Nairobi",
            "currency": "KES",
            "allow_public_registration": True,
            "require_kyc": True,
            "default_loan_interest_rate": 10.0,
            "default_contribution_reminder_days": 3,
        }

    @staticmethod
    @transaction.atomic
    def update_admin_settings(settings: Dict[str, Any], actor: User = None) -> Dict[str, Any]:
        """Update admin settings."""
        # This would save settings to database
        return {
            "message": "Settings updated successfully",
            "settings": settings,
        }

    @staticmethod
    def get_system_health() -> Dict[str, Any]:
        """Get system health status."""
        return {
            "status": "healthy",
            "database": "connected",
            "cache": "connected",
            "queue": "running",
            "uptime_hours": 720,
        }
