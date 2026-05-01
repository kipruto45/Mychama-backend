"""
M-Pesa Integration Service

Manages M-Pesa STK push, callbacks, and reconciliation.
"""

import hashlib
import logging

import requests
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.chama.models import Chama

logger = logging.getLogger(__name__)


class MpesaService:
    """Service for M-Pesa integration."""

    # M-Pesa API configuration
    BASE_URL = getattr(settings, 'MPESA_BASE_URL', 'https://sandbox.safaricom.co.ke')
    CONSUMER_KEY = getattr(settings, 'MPESA_CONSUMER_KEY', '')
    CONSUMER_SECRET = getattr(settings, 'MPESA_CONSUMER_SECRET', '')
    SHORTCODE = getattr(settings, 'MPESA_SHORTCODE', '')
    PASSKEY = getattr(settings, 'MPESA_PASSKEY', '')
    CALLBACK_URL = getattr(settings, 'MPESA_CALLBACK_URL', '')

    @staticmethod
    def get_access_token() -> str | None:
        """
        Get M-Pesa API access token.
        """
        try:
            url = f"{MpesaService.BASE_URL}/oauth/v1/generate?grant_type=client_credentials"
            
            response = requests.get(
                url,
                auth=(MpesaService.CONSUMER_KEY, MpesaService.CONSUMER_SECRET),
                timeout=10,
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('access_token')
            else:
                logger.error(f"Failed to get M-Pesa access token: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error getting M-Pesa access token: {e}")
            return None

    @staticmethod
    def generate_password() -> tuple[str, str]:
        """
        Generate M-Pesa API password.
        Returns (password, timestamp).
        """
        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        password_string = f"{MpesaService.SHORTCODE}{MpesaService.PASSKEY}{timestamp}"
        password = hashlib.sha256(password_string.encode()).hexdigest()
        return password, timestamp

    @staticmethod
    @transaction.atomic
    def initiate_stk_push(
        phone_number: str,
        amount: float,
        account_reference: str,
        transaction_desc: str,
        chama: Chama,
        user: User,
    ) -> dict:
        """
        Initiate M-Pesa STK push.
        Returns checkout request details.
        """
        from apps.payments.models import MpesaCheckoutRequest

        # Validate phone number
        if not phone_number.startswith('+254') and not phone_number.startswith('254'):
            raise ValueError("Invalid phone number format")

        # Normalize phone number
        if phone_number.startswith('+'):
            phone_number = phone_number[1:]

        # Get access token
        access_token = MpesaService.get_access_token()
        if not access_token:
            raise Exception("Failed to get M-Pesa access token")

        # Generate password
        password, timestamp = MpesaService.generate_password()

        # Prepare request
        url = f"{MpesaService.BASE_URL}/mpesa/stkpush/v1/processrequest"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }

        payload = {
            'BusinessShortCode': MpesaService.SHORTCODE,
            'Password': password,
            'Timestamp': timestamp,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone_number,
            'PartyB': MpesaService.SHORTCODE,
            'PhoneNumber': phone_number,
            'CallBackURL': MpesaService.CALLBACK_URL,
            'AccountReference': account_reference,
            'TransactionDesc': transaction_desc,
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response_data = response.json()

            if response.status_code == 200 and response_data.get('ResponseCode') == '0':
                # Create checkout request record
                checkout_request = MpesaCheckoutRequest.objects.create(
                    chama=chama,
                    user=user,
                    checkout_request_id=response_data.get('CheckoutRequestID'),
                    merchant_request_id=response_data.get('MerchantRequestID'),
                    phone_number=phone_number,
                    amount=amount,
                    account_reference=account_reference,
                    transaction_desc=transaction_desc,
                    status='pending',
                )

                logger.info(
                    f"M-Pesa STK push initiated: {checkout_request.checkout_request_id} "
                    f"for {user.full_name}"
                )

                return {
                    'checkout_request_id': checkout_request.checkout_request_id,
                    'merchant_request_id': checkout_request.merchant_request_id,
                    'status': 'pending',
                }
            else:
                error_message = response_data.get('errorMessage', 'Unknown error')
                logger.error(f"M-Pesa STK push failed: {error_message}")
                raise Exception(f"M-Pesa STK push failed: {error_message}")

        except requests.RequestException as e:
            logger.error(f"M-Pesa API request failed: {e}")
            raise Exception(f"M-Pesa API request failed: {e}")

    @staticmethod
    @transaction.atomic
    def process_callback(callback_data: dict) -> tuple[bool, str]:
        """
        Process M-Pesa callback.
        Returns (success, message).
        """
        from apps.payments.models import MpesaCallback, MpesaCheckoutRequest

        try:
            # Extract callback data
            stk_callback = callback_data.get('Body', {}).get('stkCallback', {})
            checkout_request_id = stk_callback.get('CheckoutRequestID')
            result_code = stk_callback.get('ResultCode')
            result_desc = stk_callback.get('ResultDesc')

            # Find checkout request
            try:
                checkout_request = MpesaCheckoutRequest.objects.get(
                    checkout_request_id=checkout_request_id,
                )
            except MpesaCheckoutRequest.DoesNotExist:
                logger.warning(f"Checkout request not found: {checkout_request_id}")
                return False, "Checkout request not found"

            # Create callback record
            callback = MpesaCallback.objects.create(
                checkout_request=checkout_request,
                result_code=result_code,
                result_desc=result_desc,
                callback_data=callback_data,
            )

            # Process based on result code
            if result_code == 0:
                # Payment successful
                callback_items = stk_callback.get('CallbackMetadata', {}).get('Item', [])
                
                # Extract transaction details
                mpesa_receipt = None
                transaction_date = None
                
                for item in callback_items:
                    if item.get('Name') == 'MpesaReceiptNumber':
                        mpesa_receipt = item.get('Value')
                    elif item.get('Name') == 'TransactionDate':
                        transaction_date = item.get('Value')

                # Update checkout request
                checkout_request.status = 'completed'
                checkout_request.mpesa_receipt = mpesa_receipt
                checkout_request.transaction_date = transaction_date
                checkout_request.completed_at = timezone.now()
                checkout_request.save(update_fields=[
                    'status',
                    'mpesa_receipt',
                    'transaction_date',
                    'completed_at',
                    'updated_at',
                ])

                # Update callback
                callback.mpesa_receipt = mpesa_receipt
                callback.save(update_fields=['mpesa_receipt', 'updated_at'])

                # Update payment intent
                from apps.payments.models import PaymentIntent
                payment_intent = PaymentIntent.objects.filter(
                    reference=checkout_request.account_reference,
                ).first()

                if payment_intent:
                    payment_intent.status = 'completed'
                    payment_intent.save(update_fields=['status', 'updated_at'])

                    # Update account balance
                    from apps.finance.models import Account
                    account = Account.objects.get(
                        chama=checkout_request.chama,
                        account_type='main',
                    )
                    account.balance += checkout_request.amount
                    account.save(update_fields=['balance', 'updated_at'])

                logger.info(
                    f"M-Pesa payment successful: {checkout_request_id} "
                    f"Receipt: {mpesa_receipt}"
                )

                return True, "Payment successful"

            else:
                # Payment failed
                checkout_request.status = 'failed'
                checkout_request.result_desc = result_desc
                checkout_request.save(update_fields=['status', 'result_desc', 'updated_at'])

                logger.warning(
                    f"M-Pesa payment failed: {checkout_request_id} "
                    f"Result: {result_code} - {result_desc}"
                )

                return False, f"Payment failed: {result_desc}"

        except Exception as e:
            logger.error(f"Error processing M-Pesa callback: {e}")
            return False, f"Error processing callback: {e}"

    @staticmethod
    def query_stk_status(checkout_request_id: str) -> dict:
        """
        Query M-Pesa STK push status.
        """
        from apps.payments.models import MpesaCheckoutRequest

        try:
            checkout_request = MpesaCheckoutRequest.objects.get(
                checkout_request_id=checkout_request_id,
            )

            return {
                'checkout_request_id': checkout_request.checkout_request_id,
                'status': checkout_request.status,
                'mpesa_receipt': checkout_request.mpesa_receipt,
                'transaction_date': checkout_request.transaction_date,
                'result_desc': checkout_request.result_desc,
                'created_at': checkout_request.created_at.isoformat(),
                'completed_at': checkout_request.completed_at.isoformat() if checkout_request.completed_at else None,
            }

        except MpesaCheckoutRequest.DoesNotExist:
            return {'error': 'Checkout request not found'}

    @staticmethod
    def get_pending_payments(chama: Chama = None) -> list[dict]:
        """
        Get pending M-Pesa payments.
        """
        from apps.payments.models import MpesaCheckoutRequest

        queryset = MpesaCheckoutRequest.objects.filter(status='pending')

        if chama:
            queryset = queryset.filter(chama=chama)

        requests = queryset.order_by('-created_at')

        return [
            {
                'checkout_request_id': req.checkout_request_id,
                'phone_number': req.phone_number,
                'amount': req.amount,
                'account_reference': req.account_reference,
                'user_name': req.user.full_name,
                'chama_name': req.chama.name if req.chama else None,
                'created_at': req.created_at.isoformat(),
            }
            for req in requests
        ]

    @staticmethod
    def reconcile_payments(chama: Chama) -> dict:
        """
        Reconcile M-Pesa payments for a chama.
        """
        from django.db.models import Count, Sum

        from apps.payments.models import MpesaCheckoutRequest

        # Get all payments
        payments = MpesaCheckoutRequest.objects.filter(chama=chama)

        summary = payments.aggregate(
            total=Count('id'),
            completed=Count('id', filter=models.Q(status='completed')),
            pending=Count('id', filter=models.Q(status='pending')),
            failed=Count('id', filter=models.Q(status='failed')),
            total_amount=Sum('amount', filter=models.Q(status='completed')),
        )

        # Get unresolved payments (pending for more than 1 hour)
        one_hour_ago = timezone.now() - timezone.timedelta(hours=1)
        unresolved = payments.filter(
            status='pending',
            created_at__lt=one_hour_ago,
        ).count()

        return {
            'total_payments': summary['total'] or 0,
            'completed_payments': summary['completed'] or 0,
            'pending_payments': summary['pending'] or 0,
            'failed_payments': summary['failed'] or 0,
            'total_amount': summary['total_amount'] or 0,
            'unresolved_payments': unresolved,
            'success_rate': (
                (summary['completed'] / summary['total'] * 100)
                if summary['total'] > 0 else 0
            ),
        }
