from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from apps.accounts.models import MemberCard
from apps.chama.forms import (
    ChamaForm,
    InviteLinkCreateForm,
    JoinChamaForm,
    MembershipReviewActionForm,
)
from apps.chama.models import (
    Chama,
    InviteLink,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MembershipRole,
    MemberStatus,
)
from apps.chama.serializers import RequestJoinSerializer
from apps.chama.services import get_effective_role
from core.audit import create_activity_log, create_audit_log

MANAGER_ROLES = {
    MembershipRole.CHAMA_ADMIN,
    MembershipRole.SECRETARY,
}


@dataclass
class ActivityItem:
    icon: str
    title: str
    description: str
    timestamp: datetime


class ActivityCollection:
    def __init__(self, items: list[ActivityItem]):
        self._items = items

    def all(self):
        return self._items


def _active_membership_for_request(request):
    scoped_chama_id = request.GET.get("chama_id") or request.session.get("active_chama_id")
    memberships = Membership.objects.select_related("chama").filter(
        user=request.user,
        is_active=True,
        is_approved=True,
        status=MemberStatus.ACTIVE,
        exited_at__isnull=True,
    )

    membership = None
    if scoped_chama_id:
        membership = memberships.filter(chama_id=scoped_chama_id).first()

    if membership is None:
        membership = memberships.order_by("joined_at").first()

    if membership:
        request.session["active_chama_id"] = str(membership.chama_id)

    return membership


def _is_reviewer(role: str | None) -> bool:
    return role in {
        MembershipRole.CHAMA_ADMIN,
        MembershipRole.ADMIN,
        
        MembershipRole.SECRETARY,
    }


def _latest_request_for_user(user, chama_id=None):
    queryset = MembershipRequest.objects.select_related("chama", "reviewed_by").filter(
        user=user
    )
    if chama_id:
        queryset = queryset.filter(chama_id=chama_id)
    return queryset.order_by("-created_at").first()


@method_decorator(login_required, name="dispatch")
class MemberListView(TemplateView):
    template_name = "chama/member_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = _active_membership_for_request(self.request)
        context["title"] = "Chama Members"

        if not membership:
            context["chama"] = None
            context["members"] = Paginator([], 12).get_page(1)
            return context

        chama = membership.chama
        queryset = Membership.objects.select_related("user").filter(
            chama=chama,
            exited_at__isnull=True,
        )

        if (
            get_effective_role(self.request.user, chama.id, membership)
            != MembershipRole.CHAMA_ADMIN
        ):
            queryset = queryset.filter(is_approved=True)

        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(user__full_name__icontains=query)
                | Q(user__phone__icontains=query)
                | Q(user__email__icontains=query)
            )

        role = self.request.GET.get("role", "").strip()
        if role:
            queryset = queryset.filter(role__iexact=role)

        status_filter = self.request.GET.get("status", "").strip().lower()
        if status_filter == "active":
            queryset = queryset.filter(
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
            )
        elif status_filter == "inactive":
            queryset = queryset.filter(is_active=False)
        elif status_filter == "pending":
            queryset = queryset.filter(is_approved=False)

        queryset = queryset.order_by("-joined_at")
        page = self.request.GET.get("page")
        members = Paginator(queryset, 12).get_page(page)

        user_ids = [member.user_id for member in members.object_list]
        contributions_map: dict = {}
        loans_map: dict = {}

        if user_ids:
            from apps.finance.models import Contribution, Loan

            contribution_stats = (
                Contribution.objects.filter(chama=chama, member_id__in=user_ids)
                .values("member_id")
                .annotate(
                    total_contributed=Coalesce(
                        Sum("amount"),
                        Value(
                            Decimal("0.00"),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        ),
                    ),
                    contributions_count=Count("id"),
                )
            )
            contributions_map = {
                row["member_id"]: {
                    "total_contributed": row["total_contributed"],
                    "contributions_count": row["contributions_count"],
                }
                for row in contribution_stats
            }

            loan_stats = (
                Loan.objects.filter(chama=chama, member_id__in=user_ids)
                .values("member_id")
                .annotate(loans_count=Count("id"))
            )
            loans_map = {
                row["member_id"]: row["loans_count"]
                for row in loan_stats
            }

        for member in members.object_list:
            member.phone_number = member.user.phone
            member.date_joined = member.joined_at
            member.contributions_count = contributions_map.get(member.user_id, {}).get(
                "contributions_count",
                0,
            )
            member.total_contributed = contributions_map.get(member.user_id, {}).get(
                "total_contributed",
                Decimal("0.00"),
            )
            member.loans_count = loans_map.get(member.user_id, 0)

        context["chama"] = chama
        context["members"] = members
        context["active_membership"] = membership
        context["can_manage_members"] = (
            get_effective_role(self.request.user, chama.id, membership) in MANAGER_ROLES
        )
        context["current_filters"] = {
            "q": query,
            "role": role,
            "status": status_filter,
        }
        return context


@method_decorator(login_required, name="dispatch")
class MemberDetailView(TemplateView):
    template_name = "chama/member_detail.html"

    def _get_context_membership(self):
        membership = _active_membership_for_request(self.request)
        if not membership:
            return None, None

        member = get_object_or_404(
            Membership.objects.select_related("user", "chama"),
            id=self.kwargs["member_id"],
            chama=membership.chama,
            exited_at__isnull=True,
        )
        return membership, member

    def post(self, request, *args, **kwargs):
        acting_membership, target_membership = self._get_context_membership()
        if not acting_membership:
            messages.error(request, "You are not an active approved member of any chama.")
            return redirect("chama:chama_create")

        if (
            get_effective_role(request.user, acting_membership.chama_id, acting_membership)
            != MembershipRole.CHAMA_ADMIN
        ):
            return HttpResponseForbidden("Only chama admins can update member status.")

        action = request.POST.get("action")
        if action == "deactivate":
            target_membership.status = MemberStatus.SUSPENDED
            target_membership.suspension_reason = "Suspended by chama admin."
            target_membership.is_active = False
            target_membership.updated_by = request.user
            target_membership.save(
                update_fields=[
                    "status",
                    "suspension_reason",
                    "is_active",
                    "updated_by",
                    "updated_at",
                ]
            )
            messages.success(request, "Member deactivated successfully.")
        elif action == "activate":
            target_membership.status = MemberStatus.ACTIVE
            target_membership.suspension_reason = ""
            target_membership.is_active = True
            target_membership.updated_by = request.user
            target_membership.save(
                update_fields=[
                    "status",
                    "suspension_reason",
                    "is_active",
                    "updated_by",
                    "updated_at",
                ]
            )
            messages.success(request, "Member activated successfully.")
        else:
            messages.error(request, "Invalid member action.")

        return redirect("chama:member_detail", member_id=target_membership.id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        acting_membership, member = self._get_context_membership()
        if not acting_membership:
            context["member"] = None
            return context

        chama = acting_membership.chama
        from apps.finance.models import Contribution, InstallmentStatus, Loan
        from apps.meetings.models import Attendance, AttendanceStatus

        contributions = (
            Contribution.objects.filter(chama=chama, member=member.user)
            .select_related("contribution_type")
            .order_by("-date_paid")[:10]
        )
        for contribution in contributions:
            contribution.date = contribution.date_paid
            contribution.reference = contribution.receipt_code

        loans = (
            Loan.objects.filter(chama=chama, member=member.user)
            .prefetch_related("installments")
            .order_by("-requested_at")[:10]
        )
        for loan in loans:
            loan.application_date = loan.requested_at
            loan.amount = loan.principal
            next_due = (
                loan.installments.filter(status__in=[InstallmentStatus.DUE, InstallmentStatus.OVERDUE])
                .order_by("due_date")
                .first()
            )
            loan.due_date = next_due.due_date if next_due else None
            if loan.status == "cleared":
                loan.repayment_status = "cleared"
            elif loan.installments.filter(status=InstallmentStatus.OVERDUE).exists():
                loan.repayment_status = "overdue"
            else:
                loan.repayment_status = "ongoing"

        contribution_count = Contribution.objects.filter(chama=chama, member=member.user).count()
        contribution_total = Contribution.objects.filter(chama=chama, member=member.user).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(
                    Decimal("0.00"),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
        )["total"]
        loan_count = Loan.objects.filter(chama=chama, member=member.user).count()
        meetings_attended = Attendance.objects.filter(
            meeting__chama=chama,
            member=member.user,
            status=AttendanceStatus.PRESENT,
        ).count()

        activity_items: list[ActivityItem] = []
        for contribution in contributions[:5]:
            activity_items.append(
                ActivityItem(
                    icon="money-bill-wave",
                    title="Contribution received",
                    description=(
                        f"KES {contribution.amount:,.2f} via "
                        f"{contribution.contribution_type.name}"
                    ),
                    timestamp=datetime.combine(
                        contribution.date_paid,
                        datetime.min.time(),
                        tzinfo=timezone.get_current_timezone(),
                    ),
                )
            )
        for loan in loans[:5]:
            activity_items.append(
                ActivityItem(
                    icon="hand-holding-dollar",
                    title="Loan update",
                    description=f"Loan status: {loan.status.replace('_', ' ').title()}",
                    timestamp=loan.requested_at,
                )
            )
        activity_items.sort(key=lambda item: item.timestamp, reverse=True)

        member.phone_number = member.user.phone
        member.date_joined = member.joined_at
        member.contributions_count = contribution_count
        member.total_contributed = contribution_total
        member.loans_count = loan_count
        member.meetings_attended = meetings_attended
        member.contributions = contributions
        member.loans = loans
        member.activities = ActivityCollection(activity_items)

        context["member"] = member
        context["chama"] = chama
        context["title"] = f"Member: {member.user.get_full_name()}"
        context["can_manage_members"] = (
            get_effective_role(
                self.request.user,
                acting_membership.chama_id,
                acting_membership,
            )
            == MembershipRole.CHAMA_ADMIN
        )
        return context


@method_decorator(login_required, name="dispatch")
class ChamaSettingsView(TemplateView):
    template_name = "chama/chama_settings.html"

    def _get_membership(self):
        return _active_membership_for_request(self.request)

    def post(self, request, *args, **kwargs):
        membership = self._get_membership()
        if not membership:
            messages.error(request, "You are not an active approved member of any chama.")
            return redirect("chama:chama_create")

        if (
            get_effective_role(request.user, membership.chama_id, membership)
            != MembershipRole.CHAMA_ADMIN
        ):
            return HttpResponseForbidden("Only chama admins can update chama settings.")

        form = ChamaForm(request.POST, instance=membership.chama)
        if form.is_valid():
            chama = form.save(commit=False)
            chama.updated_by = request.user
            chama.save()
            messages.success(request, "Chama settings updated successfully.")
            return redirect("chama:chama_settings")

        context = self.get_context_data()
        context["form"] = form
        messages.error(request, "Please correct the highlighted errors.")
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = self._get_membership()
        if membership:
            chama = membership.chama
            request_role = get_effective_role(
                self.request.user,
                membership.chama_id,
                membership,
            )
        else:
            chama = None
            request_role = None

        context["chama"] = chama
        context["title"] = "Chama Settings"
        context["form"] = kwargs.get("form") or (ChamaForm(instance=chama) if chama else ChamaForm())
        context["can_edit_settings"] = request_role == MembershipRole.CHAMA_ADMIN
        return context


@method_decorator(login_required, name="dispatch")
class ChamaCreateView(TemplateView):
    template_name = "chama/chama_create.html"

    def post(self, request, *args, **kwargs):
        form = ChamaForm(request.POST)
        if form.is_valid():
            chama = form.save(commit=False)
            chama.created_by = request.user
            chama.updated_by = request.user
            chama.save()

            now = timezone.now()
            Membership.objects.create(
                user=request.user,
                chama=chama,
                role=MembershipRole.CHAMA_ADMIN,
                status=MemberStatus.ACTIVE,
                is_active=True,
                is_approved=True,
                joined_at=now,
                approved_at=now,
                approved_by=request.user,
                created_by=request.user,
                updated_by=request.user,
            )
            request.session["active_chama_id"] = str(chama.id)
            messages.success(request, f"Chama '{chama.name}' created successfully.")
            return redirect("chama:member_list")

        context = self.get_context_data(form=form)
        messages.error(request, "Please correct the highlighted errors.")
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Chama"
        context["form"] = kwargs.get("form") or ChamaForm()
        return context


@method_decorator(login_required, name="dispatch")
class JoinChamaView(TemplateView):
    template_name = "chama/join_chama.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Join Chama"
        context["form"] = kwargs.get("form") or JoinChamaForm()
        context["available_chamas"] = Chama.objects.filter(
            status="active"
        ).order_by("name")
        context["active_memberships"] = Membership.objects.select_related("chama").filter(
            user=self.request.user,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        )
        context["pending_requests"] = MembershipRequest.objects.select_related("chama").filter(
            user=self.request.user,
            status=MembershipRequestStatus.PENDING,
        ).order_by("-created_at")
        return context

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = JoinChamaForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please correct the highlighted fields.")
            return self.render_to_response(self.get_context_data(form=form))

        chama = get_object_or_404(Chama, id=form.cleaned_data["chama_id"])

        if Membership.objects.filter(
            user=request.user,
            chama=chama,
            is_active=True,
            is_approved=True,
            status=MemberStatus.ACTIVE,
            exited_at__isnull=True,
        ).exists():
            messages.info(request, f"You are already an active member of {chama.name}.")
            return redirect("chama:member_list")

        serializer = RequestJoinSerializer(
            data={
                "request_note": form.cleaned_data.get("request_note", ""),
                "invite_token": form.cleaned_data.get("invite_token", ""),
                "join_code": form.cleaned_data.get("join_code", ""),
            },
            context={"chama": chama, "user": request.user},
        )
        if not serializer.is_valid():
            detail = serializer.errors
            if isinstance(detail, dict):
                first_key = next(iter(detail.keys()), "detail")
                message = detail[first_key]
                if isinstance(message, list):
                    message = message[0]
            else:
                message = "Unable to submit request."
            messages.error(request, str(message))
            return self.render_to_response(self.get_context_data(form=form))

        now = timezone.now()
        MembershipRequest.objects.filter(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            expires_at__lte=now,
        ).update(status=MembershipRequestStatus.EXPIRED, updated_by_id=request.user.id)

        pending_request = MembershipRequest.objects.filter(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            expires_at__gt=now,
        ).first()
        if pending_request:
            messages.info(
                request,
                f"You already have a pending request for {chama.name}.",
            )
            return redirect("chama:join_pending")

        expiry_days = max(1, int(getattr(settings, "MEMBERSHIP_REQUEST_EXPIRY_DAYS", 7)))
        membership_request = MembershipRequest.objects.create(
            user=request.user,
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            request_note=form.cleaned_data.get("request_note", ""),
            ip_address=request.META.get("REMOTE_ADDR"),
            device_info=str(request.META.get("HTTP_USER_AGENT", ""))[:255],
            expires_at=now + timedelta(days=expiry_days),
            created_by=request.user,
            updated_by=request.user,
        )

        invite_link = serializer.validated_data.get("invite_link")
        if invite_link:
            invite_link.current_uses = invite_link.current_uses + 1
            if invite_link.max_uses and invite_link.current_uses >= invite_link.max_uses:
                invite_link.is_active = False
            invite_link.save(update_fields=["current_uses", "is_active", "updated_at"])

        create_activity_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_submitted",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={"source": "frontend"},
        )
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="membership_request_created",
            entity_type="MembershipRequest",
            entity_id=membership_request.id,
            metadata={"status": membership_request.status, "source": "frontend"},
        )

        try:
            from apps.ai.membership_review import process_membership_ai_review

            process_membership_ai_review.delay(str(membership_request.id))
        except Exception:  # noqa: BLE001
            pass

        messages.success(
            request,
            f"Join request submitted for {chama.name}. Awaiting approval.",
        )
        return redirect("chama:join_pending")


@method_decorator(login_required, name="dispatch")
class JoinStatusView(TemplateView):
    template_name = "chama/join_status.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status_slug = str(self.kwargs.get("status_slug", "pending")).strip().lower()
        status_slug = status_slug.replace("_", "-")
        active_membership = _active_membership_for_request(self.request)
        latest_request = _latest_request_for_user(self.request.user)

        effective_status = status_slug
        if active_membership:
            effective_status = "approved"
        elif latest_request:
            mapping = {
                MembershipRequestStatus.PENDING: "pending",
                MembershipRequestStatus.NEEDS_INFO: "needs-info",
                MembershipRequestStatus.REJECTED: "rejected",
                MembershipRequestStatus.EXPIRED: "expired",
                MembershipRequestStatus.CANCELLED: "cancelled",
            }
            effective_status = mapping.get(latest_request.status, status_slug)

        if active_membership and active_membership.status == MemberStatus.SUSPENDED:
            effective_status = "suspended"

        status_meta = {
            "pending": {
                "title": "Membership Request Pending",
                "description": "Your request is awaiting review by the chama secretary/admin.",
            },
            "needs-info": {
                "title": "More Information Needed",
                "description": "Your chama reviewers requested additional information.",
            },
            "rejected": {
                "title": "Membership Request Rejected",
                "description": "Your request was not approved for this chama.",
            },
            "expired": {
                "title": "Membership Request Expired",
                "description": "Your pending request expired. You can submit a new one.",
            },
            "cancelled": {
                "title": "Membership Request Cancelled",
                "description": "This request was cancelled.",
            },
            "approved": {
                "title": "Membership Approved",
                "description": "Your membership is active. You can access your dashboard.",
            },
            "suspended": {
                "title": "Membership Suspended",
                "description": "Your membership is suspended. Contact chama leadership.",
            },
        }

        context["status_slug"] = effective_status
        context["status_meta"] = status_meta.get(
            effective_status,
            status_meta["pending"],
        )
        context["latest_request"] = latest_request
        context["active_membership"] = active_membership
        return context


@method_decorator(login_required, name="dispatch")
class MembershipRequestsBoardView(TemplateView):
    template_name = "chama/membership_requests.html"

    def _resolve_context(self):
        membership = _active_membership_for_request(self.request)
        if not membership:
            return None, None, None
        role = get_effective_role(self.request.user, membership.chama_id, membership)
        return membership, membership.chama, role

    def _query_requests(self, chama):
        MembershipRequest.objects.filter(
            chama=chama,
            status=MembershipRequestStatus.PENDING,
            expires_at__lte=timezone.now(),
        ).update(status=MembershipRequestStatus.EXPIRED, updated_by_id=self.request.user.id)

        queryset = MembershipRequest.objects.select_related(
            "user", "reviewed_by"
        ).filter(chama=chama)
        status_filter = str(self.request.GET.get("status", "")).strip().lower()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership, chama, role = self._resolve_context()
        status_filter = str(self.request.GET.get("status", "")).strip().lower()
        context["title"] = "Membership Requests"
        context["active_membership"] = membership
        context["chama"] = chama
        context["role"] = role
        context["can_review"] = _is_reviewer(role)
        context["status_filter"] = status_filter
        context["review_form"] = kwargs.get("review_form") or MembershipReviewActionForm()
        context["invite_form"] = kwargs.get("invite_form") or InviteLinkCreateForm()
        context["requests"] = self._query_requests(chama) if chama else []
        context["invite_links"] = (
            InviteLink.objects.filter(chama=chama).order_by("-created_at")
            if chama
            else []
        )
        return context

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        membership, chama, role = self._resolve_context()
        if not membership or not _is_reviewer(role):
            return HttpResponseForbidden(
                "Only secretary/admin can review membership requests."
            )

        form_type = str(request.POST.get("form_type", "")).strip()
        if form_type == "review_request":
            action_form = MembershipReviewActionForm(request.POST)
            request_id = request.POST.get("membership_request_id")
            membership_request = get_object_or_404(
                MembershipRequest.objects.select_related("user"),
                id=request_id,
                chama=chama,
            )
            if not action_form.is_valid():
                messages.error(request, "Invalid review action payload.")
                return self.render_to_response(
                    self.get_context_data(review_form=action_form)
                )

            decision = action_form.cleaned_data["decision"]
            note = action_form.cleaned_data.get("note", "").strip()
            if decision == "approve":
                if not membership_request.user.phone_verified:
                    messages.error(
                        request,
                        "Cannot approve: member phone is not verified.",
                    )
                    return redirect("chama:membership_requests")
                member_record, _ = Membership.objects.get_or_create(
                    user=membership_request.user,
                    chama=chama,
                    defaults={
                        "role": MembershipRole.MEMBER,
                        "status": MemberStatus.PENDING,
                        "is_active": False,
                        "is_approved": False,
                        "joined_at": timezone.now(),
                        "created_by": request.user,
                        "updated_by": request.user,
                    },
                )
                member_record.role = MembershipRole.MEMBER
                member_record.status = MemberStatus.ACTIVE
                member_record.is_active = True
                member_record.is_approved = True
                member_record.suspension_reason = ""
                member_record.exit_reason = ""
                member_record.approved_by = request.user
                member_record.approved_at = timezone.now()
                member_record.updated_by = request.user
                member_record.exited_at = None
                member_record.save(
                    update_fields=[
                        "role",
                        "status",
                        "is_active",
                        "is_approved",
                        "suspension_reason",
                        "exit_reason",
                        "approved_by",
                        "approved_at",
                        "updated_by",
                        "exited_at",
                        "updated_at",
                    ]
                )
                membership_request.status = MembershipRequestStatus.APPROVED
                membership_request.phone_verified_at_approval = (
                    membership_request.user.phone_verified_at or timezone.now()
                )
                membership_request.reviewed_by = request.user
                membership_request.reviewed_at = timezone.now()
                membership_request.review_note = note or "Approved by reviewer."
                membership_request.updated_by = request.user
                membership_request.save(
                    update_fields=[
                        "status",
                        "phone_verified_at_approval",
                        "reviewed_by",
                        "reviewed_at",
                        "review_note",
                        "updated_by",
                        "updated_at",
                    ]
                )
                MemberCard.objects.get_or_create(
                    user=member_record.user,
                    chama=chama,
                    is_active=True,
                    defaults={
                        "card_number": (
                            f"CHM-{str(chama.id).split('-')[0].upper()}-"
                            f"{str(member_record.user_id).split('-')[0].upper()}"
                        ),
                        "qr_token": f"{membership_request.id.hex}{member_record.user_id.hex}"[:48],
                    },
                )
                create_audit_log(
                    actor=request.user,
                    chama_id=chama.id,
                    action="membership_request_approved",
                    entity_type="MembershipRequest",
                    entity_id=membership_request.id,
                    metadata={"source": "frontend"},
                )
                messages.success(request, "Membership request approved.")
            elif decision == "reject":
                membership_request.status = MembershipRequestStatus.REJECTED
                membership_request.reviewed_by = request.user
                membership_request.reviewed_at = timezone.now()
                membership_request.review_note = note or "Rejected by reviewer."
                membership_request.updated_by = request.user
                membership_request.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                        "review_note",
                        "updated_by",
                        "updated_at",
                    ]
                )
                create_audit_log(
                    actor=request.user,
                    chama_id=chama.id,
                    action="membership_request_rejected",
                    entity_type="MembershipRequest",
                    entity_id=membership_request.id,
                    metadata={"source": "frontend"},
                )
                messages.success(request, "Membership request rejected.")
            else:
                membership_request.status = MembershipRequestStatus.NEEDS_INFO
                membership_request.reviewed_by = request.user
                membership_request.reviewed_at = timezone.now()
                membership_request.review_note = note or "More information required."
                membership_request.updated_by = request.user
                membership_request.save(
                    update_fields=[
                        "status",
                        "reviewed_by",
                        "reviewed_at",
                        "review_note",
                        "updated_by",
                        "updated_at",
                    ]
                )
                create_audit_log(
                    actor=request.user,
                    chama_id=chama.id,
                    action="membership_request_needs_info",
                    entity_type="MembershipRequest",
                    entity_id=membership_request.id,
                    metadata={"source": "frontend"},
                )
                messages.success(request, "Membership request marked as needs info.")
            return redirect("chama:membership_requests")

        if form_type == "create_invite":
            invite_form = InviteLinkCreateForm(request.POST)
            if not invite_form.is_valid():
                messages.error(request, "Invalid invite link payload.")
                return self.render_to_response(
                    self.get_context_data(invite_form=invite_form)
                )

            invite_link = InviteLink.objects.create(
                chama=chama,
                token=InviteLink.generate_token(),
                created_by=request.user,
                max_uses=invite_form.cleaned_data.get("max_uses"),
                expires_at=timezone.now() + timedelta(days=invite_form.cleaned_data["expires_in_days"]),
                restricted_phone=invite_form.cleaned_data.get("restricted_phone", ""),
                preassigned_role=invite_form.cleaned_data.get("preassigned_role", ""),
                is_active=True,
                updated_by=request.user,
            )
            create_audit_log(
                actor=request.user,
                chama_id=chama.id,
                action="invite_link_created",
                entity_type="InviteLink",
                entity_id=invite_link.id,
                metadata={"source": "frontend"},
            )
            messages.success(request, "Invite link created.")
            return redirect("chama:membership_requests")

        if form_type == "invite_action":
            invite_id = request.POST.get("invite_id")
            invite_action = str(request.POST.get("invite_action", "")).strip()
            invite_link = get_object_or_404(InviteLink, id=invite_id, chama=chama)
            if invite_action == "revoke":
                invite_link.is_active = False
                invite_link.revoked_at = timezone.now()
                invite_link.revoke_reason = (
                    str(request.POST.get("reason", "")).strip() or "Revoked by reviewer."
                )
                invite_link.updated_by = request.user
                invite_link.save(
                    update_fields=[
                        "is_active",
                        "revoked_at",
                        "revoke_reason",
                        "updated_by",
                        "updated_at",
                    ]
                )
                messages.success(request, "Invite link revoked.")
            elif invite_action == "resend":
                if invite_link.expires_at <= timezone.now():
                    invite_link.expires_at = timezone.now() + timedelta(days=7)
                    invite_link.is_active = True
                    invite_link.updated_by = request.user
                    invite_link.save(
                        update_fields=["expires_at", "is_active", "updated_by", "updated_at"]
                    )
                messages.success(request, "Invite link is ready to resend.")
            return redirect("chama:membership_requests")

        messages.error(request, "Unsupported action.")
        return redirect("chama:membership_requests")


# Function-based wrappers
@login_required
def member_list_view(request):
    return MemberListView.as_view()(request)


@login_required
def member_detail_view(request, member_id):
    return MemberDetailView.as_view()(request, member_id=member_id)


@login_required
def chama_settings_view(request):
    return ChamaSettingsView.as_view()(request)


@login_required
def chama_create_view(request):
    return ChamaCreateView.as_view()(request)


@login_required
def join_chama_view(request):
    return JoinChamaView.as_view()(request)


@login_required
def join_pending_view(request):
    return JoinStatusView.as_view()(request, status_slug="pending")


@login_required
def join_status_view(request, status_slug):
    return JoinStatusView.as_view()(request, status_slug=status_slug)


@login_required
def membership_requests_view(request):
    return MembershipRequestsBoardView.as_view()(request)
