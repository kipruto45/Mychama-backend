"""
KYC (Know Your Customer) Service

Manages KYC verification workflows, document validation,
and approval processes.
"""

import logging

from django.core.files.uploadedfile import UploadedFile
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import MemberKYC, MemberKYCTier, MemberKYCStatus, User

logger = logging.getLogger(__name__)


class KYCService:
    """Service for managing KYC verification."""

    # File validation constants
    ALLOWED_MIME_TYPES = {'image/jpeg', 'image/png', 'image/jpg', 'application/pdf'}
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

    @staticmethod
    def validate_document(file: UploadedFile) -> tuple[bool, str]:
        """
        Validate uploaded KYC document.
        Returns (is_valid, error_message).
        """
        # Check file size
        if file.size > KYCService.MAX_FILE_SIZE:
            return False, f"File size exceeds maximum of {KYCService.MAX_FILE_SIZE / (1024*1024)}MB"

        # Check file type
        if file.content_type not in KYCService.ALLOWED_MIME_TYPES:
            return False, f"File type {file.content_type} not allowed. Allowed types: {', '.join(KYCService.ALLOWED_MIME_TYPES)}"

        return True, ""

    @staticmethod
    @transaction.atomic
    def submit_kyc(
        user: User,
        chama_id: str,
        id_number: str,
        id_front_image: UploadedFile,
        selfie_image: UploadedFile,
    ) -> tuple[MemberKYC, list[str]]:
        """
        Submit KYC documents for verification.
        Returns (kyc_record, errors).
        """
        errors = []

        # Validate ID number
        if not id_number or len(id_number) < 5:
            errors.append("Invalid ID number")

        # Validate documents
        is_valid, error = KYCService.validate_document(id_front_image)
        if not is_valid:
            errors.append(f"ID front image: {error}")

        is_valid, error = KYCService.validate_document(selfie_image)
        if not is_valid:
            errors.append(f"Selfie image: {error}")

        if errors:
            return None, errors

        # Check if KYC already exists
        existing_kyc = MemberKYC.objects.filter(
            user=user,
            chama_id=chama_id,
        ).first()

        if existing_kyc:
            # Update existing KYC
            existing_kyc.id_number = id_number
            existing_kyc.id_front_image = id_front_image
            existing_kyc.selfie_image = selfie_image
            existing_kyc.status = MemberKYCStatus.PENDING
            existing_kyc.reviewed_by = None
            existing_kyc.reviewed_at = None
            existing_kyc.review_note = ''
            existing_kyc.save()
            
            logger.info(f"KYC updated for user {user.id} in chama {chama_id}")
            return existing_kyc, []

        # Create new KYC record
        kyc = MemberKYC.objects.create(
            user=user,
            chama_id=chama_id,
            id_number=id_number,
            id_front_image=id_front_image,
            selfie_image=selfie_image,
            status=MemberKYCStatus.PENDING,
        )

        logger.info(f"KYC submitted for user {user.id} in chama {chama_id}")
        return kyc, []

    @staticmethod
    @transaction.atomic
    def approve_kyc(
        kyc_id: str,
        reviewer: User,
        review_note: str = '',
    ) -> tuple[bool, str]:
        """
        Approve KYC submission.
        Returns (success, message).
        """
        try:
            kyc = MemberKYC.objects.get(id=kyc_id)

            if not reviewer.is_staff and not reviewer.is_superuser:
                return False, "Only system administrators can review KYC escalations."
            
            if kyc.status not in {MemberKYCStatus.PENDING, MemberKYCStatus.UNDER_REVIEW, MemberKYCStatus.RESUBMIT_REQUIRED}:
                return False, f"KYC is not reviewable (current status: {kyc.status})"

            kyc.status = MemberKYCStatus.APPROVED
            kyc.kyc_tier = MemberKYCTier.TIER_2
            kyc.verification_score = max(kyc.verification_score, 81)
            kyc.reviewed_by = reviewer
            kyc.reviewed_at = timezone.now()
            kyc.review_note = review_note
            kyc.auto_verified_at = kyc.auto_verified_at or timezone.now()
            kyc.save()

            from apps.automations.domain_services import notify_kyc_result

            logger.info(f"KYC approved for user {kyc.user.id} by {reviewer.id}")
            notify_kyc_result(kyc_record=kyc, actor=reviewer)

            return True, "KYC approved successfully"

        except MemberKYC.DoesNotExist:
            return False, "KYC record not found"

    @staticmethod
    @transaction.atomic
    def reject_kyc(
        kyc_id: str,
        reviewer: User,
        review_note: str,
    ) -> tuple[bool, str]:
        """
        Reject KYC submission.
        Returns (success, message).
        """
        try:
            kyc = MemberKYC.objects.get(id=kyc_id)

            if not reviewer.is_staff and not reviewer.is_superuser:
                return False, "Only system administrators can review KYC escalations."
            
            if kyc.status not in {MemberKYCStatus.PENDING, MemberKYCStatus.UNDER_REVIEW, MemberKYCStatus.RESUBMIT_REQUIRED}:
                return False, f"KYC is not reviewable (current status: {kyc.status})"

            if not review_note:
                return False, "Review note is required for rejection"

            kyc.status = MemberKYCStatus.REJECTED
            kyc.kyc_tier = MemberKYCTier.TIER_0
            kyc.reviewed_by = reviewer
            kyc.reviewed_at = timezone.now()
            kyc.review_note = review_note
            kyc.rejection_attempts += 1
            kyc.last_rejection_reason = review_note
            kyc.save()

            from apps.automations.domain_services import notify_kyc_result

            logger.info(f"KYC rejected for user {kyc.user.id} by {reviewer.id}")
            notify_kyc_result(kyc_record=kyc, actor=reviewer)

            return True, "KYC rejected"

        except MemberKYC.DoesNotExist:
            return False, "KYC record not found"

    @staticmethod
    def get_kyc_status(user: User, chama_id: str) -> dict:
        """
        Get KYC status for user in chama.
        """
        try:
            kyc = MemberKYC.objects.get(
                user=user,
                chama_id=chama_id,
            )
            return {
                'status': kyc.status,
                'submitted_at': kyc.created_at,
                'reviewed_at': kyc.reviewed_at,
                'review_note': kyc.review_note,
                'reviewed_by': kyc.reviewed_by.full_name if kyc.reviewed_by else None,
            }
        except MemberKYC.DoesNotExist:
            return {
                'status': 'not_submitted',
                'submitted_at': None,
                'reviewed_at': None,
                'review_note': None,
                'reviewed_by': None,
            }

    @staticmethod
    def get_pending_kycs(chama_id: str, limit: int = 50) -> list[MemberKYC]:
        """
        Get pending KYC submissions for chama.
        """
        return list(MemberKYC.objects.filter(
            chama_id=chama_id,
            status=MemberKYCStatus.PENDING,
        ).select_related('user').order_by('-created_at')[:limit])

    @staticmethod
    def get_kyc_statistics(chama_id: str) -> dict:
        """
        Get KYC statistics for chama.
        """
        from django.db.models import Count

        stats = MemberKYC.objects.filter(
            chama_id=chama_id,
        ).aggregate(
            total=Count('id'),
            pending=Count('id', filter=models.Q(status=MemberKYCStatus.PENDING)),
            approved=Count('id', filter=models.Q(status=MemberKYCStatus.APPROVED)),
            rejected=Count('id', filter=models.Q(status=MemberKYCStatus.REJECTED)),
        )

        return {
            'total': stats['total'] or 0,
            'pending': stats['pending'] or 0,
            'approved': stats['approved'] or 0,
            'rejected': stats['rejected'] or 0,
            'approval_rate': (
                (stats['approved'] / stats['total'] * 100) 
                if stats['total'] > 0 else 0
            ),
        }
