import uuid

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import Http404, RawPostDataException
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.chama.permissions import (
    IsApprovedActiveMember,
    IsTreasurerOrAdmin,
    get_membership,
)
from apps.chama.services import get_effective_role
from apps.finance.services import FinanceService, FinanceServiceError
from apps.meetings.models import Meeting
from apps.payments.models import (
    CallbackKind,
    CallbackLog,
    MpesaCallbackLog,
    MpesaTransaction,
    MpesaTransactionStatus,
    PaymentActivityLog,
    PaymentAllocationRule,
    PaymentDispute,
    PaymentIntent,
    PaymentReconciliationRun,
    PaymentRefund,
    UssdSessionLog,
)
from apps.payments.mpesa_client import MpesaClient
from apps.payments.serializers import (
    AdminTransactionsQuerySerializer,
    B2CTimeoutPayloadSerializer,
    C2BConfirmationPayloadSerializer,
    C2BValidationPayloadSerializer,
    DepositC2BIntentSerializer,
    DepositSTKInitiateSerializer,
    InitiateMpesaSerializer,
    IntentApprovalSerializer,
    LoanRepaymentC2BIntentSerializer,
    LoanRepaymentStatusQuerySerializer,
    LoanRepaymentSTKInitiateSerializer,
    MpesaCallbackLogSerializer,
    MpesaCallbackSerializer,
    MpesaTransactionSerializer,
    PaymentActivityLogSerializer,
    PaymentAllocationRuleSerializer,
    PaymentAllocationRuleUpsertSerializer,
    PaymentDisputeCreateSerializer,
    PaymentDisputeResolveSerializer,
    PaymentDisputeSerializer,
    PaymentIntentHistorySerializer,
    PaymentIntentSerializer,
    PaymentReconciliationRunSerializer,
    PaymentRefundSerializer,
    ReconciliationRunsQuerySerializer,
    RefundDecisionSerializer,
    RefundRequestSerializer,
    SplitPaymentC2BIntentSerializer,
    SplitPaymentSTKInitiateSerializer,
    TransactionsQuerySerializer,
    UssdCallbackSerializer,
    WithdrawalRequestSerializer,
)
from apps.payments.services import (
    MpesaService,
    MpesaServiceError,
    PaymentWorkflowError,
    PaymentWorkflowService,
)
from core.audit import create_audit_log
from core.throttles import MpesaCallbackRateThrottle, PaymentInitiationRateThrottle
from core.utils import normalize_kenyan_phone


def _client_ip(request) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _resolve_chama_scope(request, explicit_chama_id=None) -> str:
    """Resolve chama scope from query/header/session and validate consistency."""
    raw_sources = [
        explicit_chama_id,
        request.query_params.get("chama_id"),
        request.headers.get("X-CHAMA-ID"),
        request.session.get("active_chama_id"),
    ]

    parsed_values: list[str] = []
    for raw in raw_sources:
        if raw in [None, ""]:
            continue
        try:
            parsed_values.append(str(uuid.UUID(str(raw))))
        except ValueError as exc:
            raise ValidationError({"detail": "Invalid chama scope."}) from exc

    if not parsed_values:
        raise ValidationError(
            {"chama_id": "Chama scope is required (query/header/session)."}
        )

    if len(set(parsed_values)) > 1:
        raise ValidationError(
            {"detail": "Chama scope values do not match across request sources."}
        )

    return parsed_values[0]


class ChamaScopeMixin:
    chama_lookup_url_kwarg = "chama_id"

    @staticmethod
    def _as_uuid(value, source_label: str) -> str | None:
        if not value:
            return None
        try:
            return str(uuid.UUID(str(value)))
        except ValueError as exc:
            raise ValidationError(
                {"detail": f"Invalid chama id in {source_label}."}
            ) from exc

    def get_scoped_chama_id(self):
        from_url = self._as_uuid(
            self.kwargs.get(self.chama_lookup_url_kwarg),
            "URL",
        )
        from_header = self._as_uuid(
            self.request.headers.get("X-CHAMA-ID"),
            "X-CHAMA-ID header",
        )
        from_query = self._as_uuid(
            self.request.query_params.get("chama_id"),
            "query parameter",
        )

        from_body = None
        if self.request.method in {"POST", "PUT", "PATCH"}:
            from_body = self._as_uuid(
                getattr(self.request, "data", {}).get("chama_id"),
                "request body",
            )

        candidates = [
            item for item in [from_url, from_header, from_query, from_body] if item
        ]
        if not candidates:
            raise ValidationError({"detail": "Chama scope is required."})

        if len(set(candidates)) > 1:
            raise ValidationError(
                {"detail": "Chama scope values do not match across request sources."}
            )

        return candidates[0]

    def get_scoped_chama(self):
        return get_object_or_404(Chama, id=self.get_scoped_chama_id())


# ---------------------------------------------------------------------------
# Legacy endpoints (retained)
# ---------------------------------------------------------------------------


class MpesaTransactionListView(ChamaScopeMixin, generics.ListAPIView):
    serializer_class = MpesaTransactionSerializer
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "phone",
        "reference",
        "checkout_request_id",
        "receipt_number",
        "status",
    ]

    def get_queryset(self):
        chama_id = self.get_scoped_chama_id()
        queryset = MpesaTransaction.objects.select_related(
            "member", "initiated_by"
        ).filter(chama_id=chama_id)

        membership = get_membership(self.request.user, chama_id)
        if membership and (
            get_effective_role(self.request.user, chama_id, membership)
            == MembershipRole.MEMBER
        ):
            queryset = queryset.filter(member=self.request.user)

        return queryset


class MpesaTransactionDetailView(ChamaScopeMixin, generics.RetrieveAPIView):
    serializer_class = MpesaTransactionSerializer
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    lookup_field = "id"
    lookup_url_kwarg = "id"

    def get_queryset(self):
        chama_id = self.get_scoped_chama_id()
        queryset = MpesaTransaction.objects.select_related(
            "member", "initiated_by"
        ).filter(chama_id=chama_id)

        membership = get_membership(self.request.user, chama_id)
        if membership and (
            get_effective_role(self.request.user, chama_id, membership)
            == MembershipRole.MEMBER
        ):
            queryset = queryset.filter(member=self.request.user)

        return queryset


class InitiateMpesaPaymentView(ChamaScopeMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsApprovedActiveMember]
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request, *args, **kwargs):
        chama = self.get_scoped_chama()
        serializer = InitiateMpesaSerializer(
            data=request.data,
            context={"request": request, "chama": chama},
        )
        serializer.is_valid(raise_exception=True)

        idempotency_key = serializer.validated_data.get("idempotency_key")
        if idempotency_key:
            existing = MpesaTransaction.objects.filter(
                chama=chama,
                idempotency_key=idempotency_key,
            ).first()
            if existing:
                return Response(
                    MpesaTransactionSerializer(existing).data, status=status.HTTP_200_OK
                )

        try:
            PaymentWorkflowService._enforce_billing_stk_access(chama)
        except PaymentWorkflowError as exc:
            return Response(
                {
                    "error": "payment_required",
                    "detail": str(exc),
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        transaction = serializer.save()

        try:
            response_payload = MpesaService.initiate_stk_push(transaction)
        except MpesaServiceError as exc:
            transaction.status = MpesaTransactionStatus.FAILED
            transaction.failure_reason = str(exc)
            transaction.updated_by = request.user
            transaction.save(
                update_fields=["status", "failure_reason", "updated_by", "updated_at"]
            )
            return Response(
                {"detail": "Unable to initiate STK push at this time."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        transaction.merchant_request_id = response_payload.get(
            "MerchantRequestID", f"MR_{uuid.uuid4().hex[:20]}"
        )
        transaction.checkout_request_id = response_payload.get(
            "CheckoutRequestID", f"ws_CO_{uuid.uuid4().hex[:24]}"
        )
        transaction.status = MpesaTransactionStatus.PENDING_CALLBACK
        transaction.updated_by = request.user
        transaction.save(
            update_fields=[
                "merchant_request_id",
                "checkout_request_id",
                "status",
                "updated_by",
                "updated_at",
            ]
        )
        PaymentWorkflowService._consume_billing_usage(chama, "stk_pushes", 1)
        create_audit_log(
            actor=request.user,
            chama_id=chama.id,
            action="mpesa_stk_initiated",
            entity_type="MpesaTransaction",
            entity_id=transaction.id,
            metadata={
                "purpose": transaction.purpose,
                "amount": str(transaction.amount),
                "phone": transaction.phone,
                "checkout_request_id": transaction.checkout_request_id,
            },
        )

        return Response(
            MpesaTransactionSerializer(transaction).data,
            status=status.HTTP_201_CREATED,
        )


class MpesaCallbackView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [MpesaCallbackRateThrottle]

    @staticmethod
    def _callback_signature_header_name() -> str:
        return getattr(
            settings,
            "MPESA_CALLBACK_SIGNATURE_HEADER",
            "X-MPESA-SIGNATURE",
        )

    def post(self, request, *args, **kwargs):
        raw_payload = b""
        try:
            raw_payload = request.body
        except RawPostDataException:
            raw_payload = str(request.data).encode("utf-8")
        payload_data = request.data

        serializer = MpesaCallbackSerializer(data=payload_data)
        serializer.is_valid(raise_exception=True)

        checkout_request_id = serializer.validated_data["checkout_request_id"]
        merchant_request_id = serializer.validated_data["merchant_request_id"]
        result_code = serializer.validated_data["result_code"]
        result_desc = serializer.validated_data["result_desc"]
        receipt_number = serializer.validated_data["receipt_number"].strip()
        source_ip = _client_ip(request)
        transaction_hint = MpesaTransaction.objects.filter(
            checkout_request_id=checkout_request_id
        ).first()

        signature = request.headers.get(self._callback_signature_header_name())
        callback_ok, callback_reason = PaymentWorkflowService.verify_callback_request(
            source_ip=source_ip,
            payload_bytes=raw_payload,
            signature=signature,
        )
        if not callback_ok:
            MpesaCallbackLog.objects.create(
                transaction=transaction_hint,
                merchant_request_id=merchant_request_id,
                checkout_request_id=checkout_request_id,
                callback_data=payload_data,
                processed=False,
                processing_error=callback_reason,
                source_ip=source_ip,
            )
            return Response(
                {"detail": "Forbidden callback source."},
                status=status.HTTP_403_FORBIDDEN,
            )

        with db_transaction.atomic():
            transaction = (
                MpesaTransaction.objects.select_for_update()
                .filter(checkout_request_id=checkout_request_id)
                .first()
            )

            callback_log = MpesaCallbackLog.objects.create(
                transaction=transaction,
                merchant_request_id=merchant_request_id,
                checkout_request_id=checkout_request_id,
                callback_data=payload_data,
                processed=False,
                source_ip=source_ip,
            )

            if not transaction:
                callback_log.processing_error = "Transaction not found."
                callback_log.save(update_fields=["processing_error", "updated_at"])
                return Response(
                    {"ResultCode": 0, "ResultDesc": "Accepted"},
                    status=status.HTTP_200_OK,
                )

            transaction.raw_callback = payload_data
            transaction.callback_received_at = timezone.now()
            if merchant_request_id:
                transaction.merchant_request_id = merchant_request_id

            if str(result_code) == "0":
                duplicate_receipt_transaction = None
                if receipt_number:
                    duplicate_receipt_transaction = (
                        MpesaTransaction.objects.filter(
                            chama=transaction.chama,
                            receipt_number=receipt_number,
                        )
                        .exclude(id=transaction.id)
                        .first()
                    )

                if duplicate_receipt_transaction:
                    transaction.status = MpesaTransactionStatus.FAILED
                    transaction.failure_reason = "Duplicate receipt already processed."
                    callback_log.processed = True
                    callback_log.processing_error = (
                        "Duplicate receipt already processed."
                    )
                else:
                    transaction.status = MpesaTransactionStatus.SUCCESS
                    transaction.receipt_number = receipt_number
                    try:
                        outcome = MpesaService.post_success_to_finance(
                            transaction, receipt_number
                        )
                        callback_log.processed = True
                        callback_log.processing_error = (
                            outcome.reason if not outcome.posted else ""
                        )
                        transaction.failure_reason = ""
                    except MpesaServiceError as exc:
                        transaction.status = MpesaTransactionStatus.FAILED
                        transaction.failure_reason = str(exc)
                        callback_log.processed = True
                        callback_log.processing_error = str(exc)
            else:
                transaction.status = MpesaTransactionStatus.FAILED
                transaction.failure_reason = result_desc or "Payment failed."
                callback_log.processed = True

            transaction.save(
                update_fields=[
                    "raw_callback",
                    "callback_received_at",
                    "merchant_request_id",
                    "status",
                    "receipt_number",
                    "failure_reason",
                    "updated_at",
                ]
            )
            callback_log.save(
                update_fields=["processed", "processing_error", "updated_at"]
            )

        return Response(
            {"ResultCode": 0, "ResultDesc": "Accepted"},
            status=status.HTTP_200_OK,
        )


class MpesaCallbackLogListView(ChamaScopeMixin, generics.ListAPIView):
    serializer_class = MpesaCallbackLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsTreasurerOrAdmin]

    def get_queryset(self):
        return MpesaCallbackLog.objects.select_related("transaction").filter(
            transaction__chama_id=self.get_scoped_chama_id()
        )


# ---------------------------------------------------------------------------
# New enterprise endpoints
# ---------------------------------------------------------------------------


class PaymentBaseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _handle_error(exc: Exception):
        if isinstance(exc, PaymentWorkflowError):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, Http404):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        raise exc


class DepositSTKInitiateView(PaymentBaseView):
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request):
        serializer = DepositSTKInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = PaymentWorkflowService.initiate_deposit_stk(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            {
                "intent": PaymentIntentSerializer(outcome["intent"]).data,
                "stk_transaction": outcome["stk_transaction"].checkout_request_id,
                "checkout_request_id": outcome["stk_transaction"].checkout_request_id,
                "created": outcome["created"],
            },
            status=(
                status.HTTP_201_CREATED if outcome["created"] else status.HTTP_200_OK
            ),
        )


class DepositC2BIntentView(PaymentBaseView):
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request):
        serializer = DepositC2BIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = PaymentWorkflowService.create_deposit_c2b_intent(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            {
                "intent": PaymentIntentSerializer(outcome["intent"]).data,
                "instructions": outcome["instructions"],
            },
            status=status.HTTP_201_CREATED,
        )


class SplitPaymentSTKInitiateView(PaymentBaseView):
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request):
        serializer = SplitPaymentSTKInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = PaymentWorkflowService.initiate_split_stk(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            {
                "intent": PaymentIntentSerializer(outcome["intent"]).data,
                "checkout_request_id": outcome["stk_transaction"].checkout_request_id,
                "created": outcome["created"],
                "allocation": (outcome["intent"].metadata or {}).get("split", {}),
            },
            status=(
                status.HTTP_201_CREATED if outcome["created"] else status.HTTP_200_OK
            ),
        )


class SplitPaymentC2BIntentView(PaymentBaseView):
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request):
        serializer = SplitPaymentC2BIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = PaymentWorkflowService.create_split_c2b_intent(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            {
                "intent": PaymentIntentSerializer(outcome["intent"]).data,
                "instructions": outcome["instructions"],
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentAllocationRuleView(PaymentBaseView):
    def get(self, request):
        serializer = PaymentAllocationRuleUpsertSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        chama_id = str(serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama_id)
        if not membership:
            return Response(
                {"detail": "Membership required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        effective_role = get_effective_role(request.user, chama_id, membership)
        if effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            return Response(
                {"detail": "Not allowed to view allocation rules."},
                status=status.HTTP_403_FORBIDDEN,
            )
        rule = PaymentAllocationRule.objects.filter(chama_id=chama_id).first()
        if not rule:
            return Response(
                {
                    "chama": chama_id,
                    "strategy": "repayment_first",
                    "repayment_ratio_percent": "50.00",
                    "is_active": True,
                }
            )
        return Response(PaymentAllocationRuleSerializer(rule).data)

    def post(self, request):
        serializer = PaymentAllocationRuleUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chama_id = str(serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama_id)
        effective_role = (
            get_effective_role(request.user, chama_id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
        }:
            return Response(
                {"detail": "Only admin/treasurer can set allocation rules."},
                status=status.HTTP_403_FORBIDDEN,
            )
        rule, _ = PaymentAllocationRule.objects.get_or_create(
            chama_id=chama_id,
            defaults={
                "created_by": request.user,
                "updated_by": request.user,
            },
        )
        rule.strategy = serializer.validated_data.get("strategy", rule.strategy)
        rule.repayment_ratio_percent = serializer.validated_data.get(
            "repayment_ratio_percent",
            rule.repayment_ratio_percent,
        )
        contribution_type_id = serializer.validated_data.get(
            "welfare_contribution_type_id"
        )
        if contribution_type_id:
            from apps.finance.models import ContributionType

            contribution_type = get_object_or_404(
                ContributionType,
                id=contribution_type_id,
                chama_id=chama_id,
            )
            rule.welfare_contribution_type = contribution_type
        rule.is_active = serializer.validated_data.get("is_active", rule.is_active)
        rule.updated_by = request.user
        rule.save()
        return Response(
            PaymentAllocationRuleSerializer(rule).data, status=status.HTTP_200_OK
        )


class UssdCallbackView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PaymentInitiationRateThrottle]

    @staticmethod
    def _menu_text():
        return (
            "CON Digital Chama\n"
            "1. Check wallet balance\n"
            "2. Request loan\n"
            "3. Next meeting reminder"
        )

    def post(self, request):
        serializer = UssdCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        phone = normalize_kenyan_phone(payload["phoneNumber"])
        text = str(payload.get("text") or "").strip()
        session_id = str(payload.get("sessionId") or "").strip()
        service_code = str(payload.get("serviceCode") or "").strip()
        tokens = [item for item in text.split("*") if item]

        user = User.objects.filter(phone=phone).first()
        chama = None
        membership = None
        if user:
            requested_chama_id = payload.get("chama_id")
            membership_queryset = Membership.objects.select_related("chama").filter(
                user=user,
                is_active=True,
                is_approved=True,
                status=MemberStatus.ACTIVE,
                exited_at__isnull=True,
            )
            if requested_chama_id:
                membership = membership_queryset.filter(
                    chama_id=requested_chama_id
                ).first()
            if not membership:
                membership = membership_queryset.first()
            if membership:
                chama = membership.chama

        response_text = ""
        processing_error = ""
        try:
            if not user or not membership or not chama:
                response_text = "END Account not found or membership inactive."
            elif not tokens:
                response_text = self._menu_text()
            elif tokens[0] == "1":
                try:
                    wallet = FinanceService.compute_wallet_balance(
                        chama_id=chama.id,
                        member_id=user.id,
                        actor=user,
                    )
                    response_text = (
                        "END Wallet balance\n"
                        f"Chama: {chama.name}\n"
                        f"Balance: KES {wallet['wallet_balance']}"
                    )
                except FinanceServiceError:
                    response_text = "END Unable to fetch wallet balance right now."
            elif tokens[0] == "2":
                if len(tokens) < 2:
                    response_text = "CON Enter loan amount as 2*amount"
                else:
                    amount = tokens[1]
                    try:
                        loan = FinanceService.request_loan(
                            payload={
                                "chama_id": chama.id,
                                "member_id": user.id,
                                "principal": amount,
                                "duration_months": 3,
                            },
                            actor=user,
                        )
                        response_text = (
                            "END Loan request submitted.\n"
                            f"Loan ID: {loan.id}\n"
                            f"Amount: KES {loan.principal}"
                        )
                    except Exception as exc:  # noqa: BLE001
                        response_text = f"END Loan request failed: {str(exc)[:120]}"
            elif tokens[0] == "3":
                next_meeting = (
                    Meeting.objects.filter(chama=chama, date__gte=timezone.now())
                    .order_by("date")
                    .first()
                )
                if next_meeting:
                    response_text = (
                        "END Next meeting\n"
                        f"{next_meeting.title}\n"
                        f"{timezone.localtime(next_meeting.date).strftime('%Y-%m-%d %H:%M')}"
                    )
                else:
                    response_text = "END No upcoming meeting found."
            else:
                response_text = self._menu_text()
        except Exception as exc:  # noqa: BLE001
            processing_error = str(exc)
            response_text = "END Request could not be processed."

        UssdSessionLog.objects.create(
            session_id=session_id,
            phone=phone,
            service_code=service_code,
            text=text,
            response_text=response_text,
            chama=chama,
            user=user,
            processed=not bool(processing_error),
            processing_error=processing_error,
        )

        return Response({"response": response_text}, status=status.HTTP_200_OK)


class LoanRepaymentSTKInitiateView(PaymentBaseView):
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request, loan_id):
        serializer = LoanRepaymentSTKInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = PaymentWorkflowService.initiate_loan_repayment_stk(
                loan_id=loan_id,
                payload=serializer.validated_data,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            {
                "intent": PaymentIntentSerializer(outcome["intent"]).data,
                "checkout_request_id": outcome["stk_transaction"].checkout_request_id,
                "created": outcome["created"],
            },
            status=(
                status.HTTP_201_CREATED if outcome["created"] else status.HTTP_200_OK
            ),
        )


class LoanRepaymentC2BIntentView(PaymentBaseView):
    throttle_classes = [PaymentInitiationRateThrottle]

    def post(self, request, loan_id):
        serializer = LoanRepaymentC2BIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = PaymentWorkflowService.create_loan_repayment_c2b_intent(
                loan_id=loan_id,
                payload=serializer.validated_data,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            {
                "intent": PaymentIntentSerializer(outcome["intent"]).data,
                "instructions": outcome["instructions"],
            },
            status=status.HTTP_201_CREATED,
        )


class LoanRepaymentStatusView(PaymentBaseView):
    def get(self, request, loan_id):
        serializer = LoanRepaymentStatusQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        try:
            data = PaymentWorkflowService.loan_repayment_status(
                loan_id=loan_id,
                actor=request.user,
                chama_id=serializer.validated_data.get("chama_id"),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(data)


class MyTransactionsView(PaymentBaseView):
    def get(self, request):
        chama_id = _resolve_chama_scope(request)
        try:
            queryset = PaymentWorkflowService.list_my_transactions(
                actor=request.user,
                chama_id=chama_id,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(PaymentIntentHistorySerializer(queryset, many=True).data)


class WithdrawalRequestView(PaymentBaseView):
    def post(self, request):
        serializer = WithdrawalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent = PaymentWorkflowService.request_withdrawal(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)

        return Response(
            PaymentIntentSerializer(intent).data, status=status.HTTP_201_CREATED
        )


class WithdrawalApproveView(PaymentBaseView):
    def post(self, request, intent_id):
        serializer = IntentApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent = PaymentWorkflowService.approve_withdrawal_intent(
                intent_id=intent_id,
                actor=request.user,
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(PaymentIntentSerializer(intent).data)


class WithdrawalSendView(PaymentBaseView):
    def post(self, request, intent_id):
        try:
            payout = PaymentWorkflowService.send_b2c_payout(
                intent_id=intent_id,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(
            {
                "intent_id": str(payout.intent_id),
                "originator_conversation_id": payout.originator_conversation_id,
                "status": payout.status,
            }
        )


class PendingLoanDisbursementListView(PaymentBaseView):
    def get(self, request):
        serializer = TransactionsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        chama = get_object_or_404(Chama, id=serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama.id)
        effective_role = (
            get_effective_role(request.user, chama.id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            return Response(
                {"detail": "Only admin/treasurer/auditor can view disbursement queue."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = PaymentWorkflowService.pending_loan_disbursements(chama_id=chama.id)
        return Response(PaymentIntentSerializer(queryset, many=True).data)


class LoanDisbursementApproveView(WithdrawalApproveView):
    pass


class LoanDisbursementSendView(WithdrawalSendView):
    pass


class LoanDisbursementRejectView(PaymentBaseView):
    """Reject a loan disbursement."""
    def post(self, request, intent_id):
        serializer = IntentApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent = PaymentWorkflowService.reject_loan_disbursement_intent(
                intent_id=intent_id,
                actor=request.user,
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(PaymentIntentSerializer(intent).data)


class RefundRequestView(PaymentBaseView):
    def post(self, request):
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refund = PaymentWorkflowService.request_refund(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(
            PaymentRefundSerializer(refund).data, status=status.HTTP_201_CREATED
        )


class RefundApproveView(PaymentBaseView):
    def post(self, request, refund_id):
        serializer = RefundDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            refund = PaymentWorkflowService.approve_refund(
                refund_id=refund_id,
                actor=request.user,
                approve=serializer.validated_data["approve"],
                note=serializer.validated_data.get("note", ""),
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(PaymentRefundSerializer(refund).data)


class RefundProcessView(PaymentBaseView):
    def post(self, request, refund_id):
        try:
            refund = PaymentWorkflowService.process_refund(
                refund_id=refund_id,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(PaymentRefundSerializer(refund).data)


class RefundListView(PaymentBaseView):
    def get(self, request):
        serializer = TransactionsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        chama = get_object_or_404(Chama, id=serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama.id)
        if not membership:
            return Response(
                {"detail": "Membership required to view refunds."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = PaymentRefund.objects.filter(chama=chama).select_related(
            "payment_intent",
            "requested_by",
            "approved_by",
            "processed_by",
        )
        if (
            get_effective_role(request.user, chama.id, membership)
            == MembershipRole.MEMBER
        ):
            queryset = queryset.filter(
                payment_intent__created_by=request.user,
            )

        return Response(
            PaymentRefundSerializer(queryset.order_by("-created_at"), many=True).data
        )


class PaymentDisputeListCreateView(PaymentBaseView):
    def get(self, request):
        serializer = TransactionsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        chama = get_object_or_404(Chama, id=serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama.id)
        if not membership:
            return Response(
                {"detail": "Membership required to view disputes."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = PaymentDispute.objects.filter(chama=chama).select_related(
            "payment_intent",
            "opened_by",
            "resolved_by",
        )
        if (
            get_effective_role(request.user, chama.id, membership)
            == MembershipRole.MEMBER
        ):
            queryset = queryset.filter(
                Q(payment_intent__created_by=request.user) | Q(opened_by=request.user)
            )

        return Response(
            PaymentDisputeSerializer(
                queryset.distinct().order_by("-created_at"), many=True
            ).data
        )

    def post(self, request):
        serializer = PaymentDisputeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            dispute = PaymentWorkflowService.open_dispute(
                serializer.validated_data,
                request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(
            PaymentDisputeSerializer(dispute).data, status=status.HTTP_201_CREATED
        )


class PaymentDisputeResolveView(PaymentBaseView):
    def post(self, request, dispute_id):
        serializer = PaymentDisputeResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            dispute = PaymentWorkflowService.resolve_dispute(
                dispute_id=dispute_id,
                payload=serializer.validated_data,
                actor=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            return self._handle_error(exc)
        return Response(PaymentDisputeSerializer(dispute).data)


class AdminTransactionsView(PaymentBaseView):
    def get(self, request):
        serializer = AdminTransactionsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        chama = get_object_or_404(Chama, id=serializer.validated_data["chama_id"])
        membership = get_membership(request.user, chama.id)
        effective_role = (
            get_effective_role(request.user, chama.id, membership)
            if membership
            else None
        )
        if not membership or effective_role not in {
            MembershipRole.CHAMA_ADMIN,
            MembershipRole.TREASURER,
            MembershipRole.AUDITOR,
        }:
            return Response(
                {"detail": "Only admin/treasurer/auditor can view admin transactions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        filters = serializer.validated_data
        queryset = PaymentWorkflowService.admin_transactions(
            chama_id=filters["chama_id"],
            status=filters.get("status"),
            intent_type=filters.get("intent_type"),
            purpose=filters.get("purpose"),
            phone=filters.get("phone"),
            receipt=filters.get("receipt"),
            search=filters.get("search"),
            member_id=filters.get("member_id"),
            loan_id=filters.get("loan_id"),
            from_date=filters.get("from_date"),
            to_date=filters.get("to_date"),
        )
        return Response(PaymentIntentHistorySerializer(queryset, many=True).data)


class PaymentIntentActivityLogView(PaymentBaseView):
    def get(self, request, intent_id):
        intent = get_object_or_404(
            PaymentIntent.objects.select_related("chama"),
            id=intent_id,
        )

        membership = get_membership(request.user, intent.chama_id)
        if not membership:
            return Response(
                {"detail": "Membership required to view payment activity."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if (
            get_effective_role(request.user, intent.chama_id, membership)
            == MembershipRole.MEMBER
        ):
            metadata_member_id = str((intent.metadata or {}).get("member_id") or "")
            own_intent = (
                intent.created_by_id == request.user.id
                or metadata_member_id == str(request.user.id)
            )
            if not own_intent:
                return Response(
                    {"detail": "Members can only view their own payment activity."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        logs = (
            PaymentActivityLog.objects.select_related("actor")
            .filter(payment_intent_id=intent_id)
            .order_by("created_at")
        )
        return Response(PaymentActivityLogSerializer(logs, many=True).data)


class ReconciliationRunsView(PaymentBaseView):
    def get(self, request):
        serializer = ReconciliationRunsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        chama_id = serializer.validated_data.get("chama_id")

        if chama_id:
            chama = get_object_or_404(Chama, id=chama_id)
            membership = get_membership(request.user, chama.id)
            effective_role = (
                get_effective_role(request.user, chama.id, membership)
                if membership
                else None
            )
            if not membership or effective_role not in {
                MembershipRole.CHAMA_ADMIN,
                MembershipRole.TREASURER,
                MembershipRole.AUDITOR,
            }:
                return Response(
                    {
                        "detail": "Only admin/treasurer/auditor can view reconciliation runs."
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        queryset = PaymentReconciliationRun.objects.all().order_by("-run_at")
        if chama_id:
            queryset = queryset.filter(chama_id=chama_id)

        return Response(PaymentReconciliationRunSerializer(queryset, many=True).data)


class AdminRegisterC2BUrlsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Only superadmin can register C2B URLs."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if getattr(settings, "MPESA_USE_STUB", True):
            return Response(
                {
                    "detail": "C2B URL registration is disabled while MPESA_USE_STUB=True.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            response_payload = MpesaClient().register_c2b_urls()
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": "Failed to register C2B URLs.", "error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        create_audit_log(
            actor=request.user,
            chama_id=None,
            action="payments_register_c2b_urls",
            entity_type="Payments",
            entity_id=None,
            metadata={
                "response_code": str(response_payload.get("ResponseCode", "")),
                "originator_conversation_id": str(
                    response_payload.get("OriginatorConversationID", "")
                ),
            },
        )

        return Response(response_payload, status=status.HTTP_200_OK)


class CallbackBaseView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [MpesaCallbackRateThrottle]

    callback_kind = None

    @staticmethod
    def _signature_header_name() -> str:
        return getattr(settings, "MPESA_CALLBACK_SIGNATURE_HEADER", "X-MPESA-SIGNATURE")

    def _verify(self, request):
        source_ip = _client_ip(request)
        raw_payload = b""
        try:
            raw_payload = request.body
        except RawPostDataException:
            # DRF may consume the stream before callback verification.
            raw_payload = str(request.data).encode("utf-8")
        signature = request.headers.get(self._signature_header_name())
        ok, reason = PaymentWorkflowService.verify_callback_request(
            source_ip=source_ip,
            payload_bytes=raw_payload,
            signature=signature,
        )
        return ok, reason, source_ip


class C2BValidationCallbackView(CallbackBaseView):
    callback_kind = CallbackKind.C2B_VALIDATION

    def post(self, request):
        serializer = C2BValidationPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=False)

        ok, reason, source_ip = self._verify(request)
        if not ok:
            CallbackLog.objects.create(
                callback_type=self.callback_kind,
                source_ip=source_ip,
                signature_valid=False,
                payload=request.data,
                headers={k: v for k, v in request.headers.items()},
                processing_error=reason,
            )
            return Response({"ResultCode": "C2B00011", "ResultDesc": "Rejected"})

        response_payload = PaymentWorkflowService.process_c2b_validation(
            request.data,
            source_ip=source_ip,
            headers={k: v for k, v in request.headers.items()},
        )
        return Response(response_payload)


class C2BConfirmationCallbackView(CallbackBaseView):
    callback_kind = CallbackKind.C2B_CONFIRMATION

    def post(self, request):
        serializer = C2BConfirmationPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=False)

        ok, reason, source_ip = self._verify(request)
        if not ok:
            CallbackLog.objects.create(
                callback_type=self.callback_kind,
                source_ip=source_ip,
                signature_valid=False,
                payload=request.data,
                headers={k: v for k, v in request.headers.items()},
                processing_error=reason,
            )
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        response_payload = PaymentWorkflowService.process_c2b_confirmation(
            request.data,
            source_ip=source_ip,
            headers={k: v for k, v in request.headers.items()},
        )
        return Response(response_payload)


class STKCallbackV2View(CallbackBaseView):
    callback_kind = CallbackKind.STK

    def post(self, request):
        serializer = MpesaCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ok, reason, source_ip = self._verify(request)
        if not ok:
            CallbackLog.objects.create(
                callback_type=self.callback_kind,
                source_ip=source_ip,
                signature_valid=False,
                payload=request.data,
                headers={k: v for k, v in request.headers.items()},
                processing_error=reason,
            )
            return Response(
                {"detail": "Forbidden callback source."},
                status=status.HTTP_403_FORBIDDEN,
            )

        response_payload = PaymentWorkflowService.process_stk_callback(
            request.data,
            source_ip=source_ip,
            headers={k: v for k, v in request.headers.items()},
        )
        return Response(response_payload)


class B2CResultCallbackView(CallbackBaseView):
    callback_kind = CallbackKind.B2C_RESULT

    def post(self, request):
        ok, reason, source_ip = self._verify(request)
        if not ok:
            CallbackLog.objects.create(
                callback_type=self.callback_kind,
                source_ip=source_ip,
                signature_valid=False,
                payload=request.data,
                headers={k: v for k, v in request.headers.items()},
                processing_error=reason,
            )
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        response_payload = PaymentWorkflowService.process_b2c_result(
            request.data,
            source_ip=source_ip,
            headers={k: v for k, v in request.headers.items()},
        )
        return Response(response_payload)


class B2CTimeoutCallbackView(CallbackBaseView):
    callback_kind = CallbackKind.B2C_TIMEOUT

    def post(self, request):
        serializer = B2CTimeoutPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=False)

        ok, reason, source_ip = self._verify(request)
        if not ok:
            CallbackLog.objects.create(
                callback_type=self.callback_kind,
                source_ip=source_ip,
                signature_valid=False,
                payload=request.data,
                headers={k: v for k, v in request.headers.items()},
                processing_error=reason,
            )
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        response_payload = PaymentWorkflowService.process_b2c_timeout(
            request.data,
            source_ip=source_ip,
            headers={k: v for k, v in request.headers.items()},
        )
        return Response(response_payload)
