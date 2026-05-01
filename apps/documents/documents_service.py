"""
Documents and File Vault Service

Manages document storage, access control, and file management.
"""

import logging

from django.db import models, transaction

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class DocumentsService:
    """Service for managing documents and file vault."""

    @staticmethod
    @transaction.atomic
    def upload_document(
        chama: Chama,
        user: User,
        file,
        document_type: str,
        title: str,
        description: str = '',
        is_public: bool = False,
    ) -> dict:
        """
        Upload a document to the vault.
        Returns document details.
        """
        from apps.documents.models import Document

        # Create document
        document = Document.objects.create(
            chama=chama,
            uploaded_by=user,
            file=file,
            document_type=document_type,
            title=title,
            description=description,
            is_public=is_public,
            file_name=file.name,
            file_size=file.size,
            file_type=file.content_type,
        )

        logger.info(
            f"Document uploaded: {title} by {user.full_name} in {chama.name}"
        )

        return {
            'id': str(document.id),
            'title': title,
            'document_type': document_type,
            'file_name': file.name,
            'file_size': file.size,
            'is_public': is_public,
            'uploaded_by': user.full_name,
            'created_at': document.created_at.isoformat(),
        }

    @staticmethod
    def get_documents(
        chama: Chama,
        user: User = None,
        document_type: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        Get documents for a chama with filtering and pagination.
        """
        from apps.documents.models import Document

        queryset = Document.objects.filter(chama=chama)

        # Filter by visibility
        if user:
            # Show public documents and user's own documents
            queryset = queryset.filter(
                models.Q(is_public=True) |
                models.Q(uploaded_by=user)
            )
        else:
            queryset = queryset.filter(is_public=True)

        if document_type:
            queryset = queryset.filter(document_type=document_type)

        queryset = queryset.order_by('-created_at')

        # Paginate
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        documents = queryset[start:end]

        return {
            'results': [
                {
                    'id': str(doc.id),
                    'title': doc.title,
                    'description': doc.description,
                    'document_type': doc.document_type,
                    'file_name': doc.file_name,
                    'file_size': doc.file_size,
                    'file_type': doc.file_type,
                    'is_public': doc.is_public,
                    'uploaded_by_name': doc.uploaded_by.full_name,
                    'created_at': doc.created_at.isoformat(),
                }
                for doc in documents
            ],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': (total + page_size - 1) // page_size,
            },
        }

    @staticmethod
    def get_document_detail(document_id: str, user: User) -> dict | None:
        """
        Get detailed document information.
        """
        from apps.documents.models import Document

        try:
            document = Document.objects.get(id=document_id)

            # Check access
            if not document.is_public and document.uploaded_by != user:
                return None

            return {
                'id': str(document.id),
                'title': document.title,
                'description': document.description,
                'document_type': document.document_type,
                'file_name': document.file_name,
                'file_size': document.file_size,
                'file_type': document.file_type,
                'file_url': document.file.url if document.file else None,
                'is_public': document.is_public,
                'chama_id': str(document.chama.id),
                'chama_name': document.chama.name,
                'uploaded_by_id': str(document.uploaded_by.id),
                'uploaded_by_name': document.uploaded_by.full_name,
                'created_at': document.created_at.isoformat(),
                'updated_at': document.updated_at.isoformat(),
            }

        except Document.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def update_document(
        document_id: str,
        user: User,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Update a document.
        Returns (success, message).
        """
        from apps.documents.models import Document

        try:
            document = Document.objects.get(id=document_id)

            # Check permission
            if document.uploaded_by != user:
                return False, "Permission denied"

            # Update fields
            for key, value in kwargs.items():
                if hasattr(document, key):
                    setattr(document, key, value)

            document.save()

            logger.info(
                f"Document updated: {document_id} by {user.full_name}"
            )

            return True, "Document updated"

        except Document.DoesNotExist:
            return False, "Document not found"

    @staticmethod
    @transaction.atomic
    def delete_document(
        document_id: str,
        user: User,
    ) -> tuple[bool, str]:
        """
        Delete a document.
        Returns (success, message).
        """
        from apps.documents.models import Document

        try:
            document = Document.objects.get(id=document_id)

            # Check permission
            if document.uploaded_by != user:
                return False, "Permission denied"

            # Delete file
            if document.file:
                document.file.delete()

            # Delete document
            document.delete()

            logger.info(
                f"Document deleted: {document_id} by {user.full_name}"
            )

            return True, "Document deleted"

        except Document.DoesNotExist:
            return False, "Document not found"

    @staticmethod
    def get_document_types() -> list[dict]:
        """
        Get available document types.
        """
        return [
            {
                'id': 'receipt',
                'name': 'Receipt',
                'description': 'Payment receipts and invoices',
            },
            {
                'id': 'meeting_minutes',
                'name': 'Meeting Minutes',
                'description': 'Meeting minutes and notes',
            },
            {
                'id': 'constitution',
                'name': 'Constitution',
                'description': 'Chama constitution and bylaws',
            },
            {
                'id': 'kyc_document',
                'name': 'KYC Document',
                'description': 'Know Your Customer documents',
            },
            {
                'id': 'loan_agreement',
                'name': 'Loan Agreement',
                'description': 'Loan agreements and contracts',
            },
            {
                'id': 'report',
                'name': 'Report',
                'description': 'Financial and activity reports',
            },
            {
                'id': 'other',
                'name': 'Other',
                'description': 'Other documents',
            },
        ]

    @staticmethod
    def get_chama_documents_summary(chama: Chama) -> dict:
        """
        Get documents summary for a chama.
        """
        from django.db.models import Count, Sum

        from apps.documents.models import Document

        summary = Document.objects.filter(chama=chama).aggregate(
            total=Count('id'),
            total_size=Sum('file_size'),
            public=Count('id', filter=models.Q(is_public=True)),
            private=Count('id', filter=models.Q(is_public=False)),
        )

        # Get by type
        by_type = Document.objects.filter(chama=chama).values(
            'document_type',
        ).annotate(
            count=Count('id'),
        )

        return {
            'total_documents': summary['total'] or 0,
            'total_size': summary['total_size'] or 0,
            'public_documents': summary['public'] or 0,
            'private_documents': summary['private'] or 0,
            'by_type': {
                item['document_type']: item['count']
                for item in by_type
            },
        }

    @staticmethod
    def search_documents(
        chama: Chama,
        query: str,
        user: User = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search documents by title or description.
        """
        from apps.documents.models import Document

        queryset = Document.objects.filter(chama=chama)

        # Filter by visibility
        if user:
            queryset = queryset.filter(
                models.Q(is_public=True) |
                models.Q(uploaded_by=user)
            )
        else:
            queryset = queryset.filter(is_public=True)

        # Search
        queryset = queryset.filter(
            models.Q(title__icontains=query) |
            models.Q(description__icontains=query)
        )

        documents = queryset.order_by('-created_at')[:limit]

        return [
            {
                'id': str(doc.id),
                'title': doc.title,
                'description': doc.description,
                'document_type': doc.document_type,
                'file_name': doc.file_name,
                'uploaded_by_name': doc.uploaded_by.full_name,
                'created_at': doc.created_at.isoformat(),
            }
            for doc in documents
        ]
