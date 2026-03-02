"""
Management command to seed notification templates with common templates.
Includes templates for all notification types and channels.
"""

from django.core.management.base import BaseCommand

from apps.notifications.models import NotificationChannel, NotificationTemplate, NotificationType


class Command(BaseCommand):
    help = "Seed notification templates with common templates for all channels"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Reset all templates before seeding",
        )
        parser.add_argument(
            "--channels",
            nargs="+",
            default=["email", "sms", "whatsapp", "telegram"],
            help="Channels to create templates for",
        )

    def handle(self, *args, **options):
        reset = options.get("reset", False)
        channels = options.get("channels", ["email", "sms", "whatsapp", "telegram"])

        if reset:
            deleted = NotificationTemplate.objects.all().delete()[0]
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing templates"))

        templates = []

        # ==========================================
        # OTP Templates
        # ==========================================
        
        # Email OTP templates
        templates.extend([
            {
                "name": "otp_login_email",
                "template_code": "OTP_LOGIN_EMAIL",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.EMAIL,
                "subject": "Your Login Verification Code",
                "body": """Your verification code is: {{ code }}

This code will expire in {{ expiry_minutes }} minutes.

If you didn't request this code, please ignore this email or contact support immediately.

Best regards,
Digital Chama Security Team
""",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                    "ip_address": "IP address of request",
                    "device": "Device/browser info",
                },
            },
            {
                "name": "otp_signup_email",
                "template_code": "OTP_SIGNUP_EMAIL",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.EMAIL,
                "subject": "Verify Your Email Address",
                "body": """Welcome to Digital Chama!

Your verification code is: {{ code }}

This code will expire in {{ expiry_minutes }} minutes.

Please enter this code to complete your registration.

Best regards,
Digital Chama Team
""",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                },
            },
            {
                "name": "otp_password_reset_email",
                "template_code": "OTP_PASSWORD_RESET_EMAIL",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.EMAIL,
                "subject": "Password Reset Verification Code",
                "body": """You requested a password reset.

Your verification code is: {{ code }}

This code will expire in {{ expiry_minutes }} minutes.

If you didn't request this, please ignore this email.

Best regards,
Digital Chama Security Team
""",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                },
            },
        ])

        # SMS OTP templates
        templates.extend([
            {
                "name": "otp_login_sms",
                "template_code": "OTP_LOGIN_SMS",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Your login code is {{ code }}. Valid for {{ expiry_minutes }} mins. Don't share.",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                },
            },
            {
                "name": "otp_signup_sms",
                "template_code": "OTP_SIGNUP_SMS",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Your verification code is {{ code }}. Valid for {{ expiry_minutes }} mins.",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                },
            },
            {
                "name": "otp_password_reset_sms",
                "template_code": "OTP_PASSWORD_RESET_SMS",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Password reset code is {{ code }}. Valid for {{ expiry_minutes }} mins.",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                },
            },
        ])

        # WhatsApp OTP templates
        templates.extend([
            {
                "name": "otp_login_whatsapp",
                "template_code": "OTP_LOGIN_WHATSAPP",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.WHATSAPP,
                "subject": "",
                "body": "🔐 *Digital Chama*\n\nYour login code is: *{{ code }}*\n\nValid for {{ expiry_minutes }} minutes.\n\nDon't share this code with anyone.",
                "variables": {
                    "code": "6-digit OTP code",
                    "expiry_minutes": "Code expiry time in minutes",
                },
            },
        ])

        # ==========================================
        # Transaction & Finance Templates
        # ==========================================
        
        templates.extend([
            {
                "name": "contribution_reminder_email",
                "template_code": "CONTRIBUTION_REMINDER_EMAIL",
                "type": NotificationType.CONTRIBUTION_REMINDER,
                "channel": NotificationChannel.EMAIL,
                "subject": "Monthly Contribution Reminder - {{ chama_name }}",
                "body": """Dear {{ member_name }},

This is a friendly reminder that your monthly contribution of KES {{ amount }} for {{ chama_name }} is due on {{ due_date }}.

Please make your payment promptly to avoid penalties.

Pay via: {{ payment_link }}

Thank you for your continued support.

Best regards,
{{ chama_name }} Management
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Contribution amount",
                    "due_date": "Due date",
                    "payment_link": "Payment URL",
                },
            },
            {
                "name": "contribution_reminder_sms",
                "template_code": "CONTRIBUTION_REMINDER_SMS",
                "type": NotificationType.CONTRIBUTION_REMINDER,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Reminder - Your contribution of KES {{ amount }} for {{ chama_name }} is due on {{ due_date }}. Pay via {{ payment_link }}",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Contribution amount",
                    "due_date": "Due date",
                    "payment_link": "Payment URL",
                },
            },
            {
                "name": "payment_received_email",
                "template_code": "PAYMENT_RECEIVED_EMAIL",
                "type": NotificationType.PAYMENT_CONFIRMATION,
                "channel": NotificationChannel.EMAIL,
                "subject": "Payment Received - {{ chama_name }}",
                "body": """Dear {{ member_name }},

We received your payment of KES {{ amount }}.

Transaction Details:
- Amount: KES {{ amount }}
- Date: {{ payment_date }}
- Reference: {{ reference }}

Thank you for your contribution to {{ chama_name }}.

Best regards,
{{ chama_name }} Treasurer
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Payment amount",
                    "payment_date": "Payment date",
                    "reference": "Payment reference",
                },
            },
            {
                "name": "payment_received_sms",
                "template_code": "PAYMENT_RECEIVED_SMS",
                "type": NotificationType.PAYMENT_CONFIRMATION,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Payment of KES {{ amount }} received. Ref: {{ reference }}. Thank you!",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Payment amount",
                    "reference": "Payment reference",
                },
            },
            {
                "name": "loan_approved_email",
                "template_code": "LOAN_APPROVED_EMAIL",
                "type": NotificationType.LOAN_UPDATE,
                "channel": NotificationChannel.EMAIL,
                "subject": "Loan Approved - {{ chama_name }}",
                "body": """Dear {{ member_name }},

Congratulations! Your loan application for KES {{ amount }} has been approved.

Loan Details:
- Approved Amount: KES {{ amount }}
- Interest Rate: {{ interest_rate }}%
- Term: {{ term_months }} months
- Monthly Payment: KES {{ monthly_payment }}

Please contact the treasurer to arrange disbursement.

Best regards,
{{ chama_name }} Management
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Loan amount",
                    "interest_rate": "Interest rate",
                    "term_months": "Loan term in months",
                    "monthly_payment": "Monthly payment amount",
                },
            },
            {
                "name": "loan_approved_sms",
                "template_code": "LOAN_APPROVED_SMS",
                "type": NotificationType.LOAN_UPDATE,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Your loan of KES {{ amount }} approved! Terms: {{ interest_rate }}% for {{ term_months }} months. Monthly: KES {{ monthly_payment }}.",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Loan amount",
                    "interest_rate": "Interest rate",
                    "term_months": "Loan term in months",
                    "monthly_payment": "Monthly payment amount",
                },
            },
        ])

        # ==========================================
        # Meeting Templates
        # ==========================================
        
        templates.extend([
            {
                "name": "meeting_reminder_email",
                "template_code": "MEETING_REMINDER_EMAIL",
                "type": NotificationType.MEETING_NOTIFICATION,
                "channel": NotificationChannel.EMAIL,
                "subject": "Meeting Reminder - {{ chama_name }}",
                "body": """Dear {{ member_name }},

This is a reminder about the upcoming {{ chama_name }} meeting.

Date: {{ meeting_date }}
Time: {{ meeting_time }}
Venue: {{ venue }}

Agenda:
{{ agenda }}

Please confirm your attendance.

Best regards,
{{ chama_name }} Secretary
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "meeting_date": "Meeting date",
                    "meeting_time": "Meeting time",
                    "venue": "Meeting venue",
                    "agenda": "Meeting agenda",
                },
            },
            {
                "name": "meeting_reminder_sms",
                "template_code": "MEETING_REMINDER_SMS",
                "type": NotificationType.MEETING_NOTIFICATION,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: Meeting reminder - {{ chama_name }} on {{ meeting_date }} at {{ meeting_time }}. Venue: {{ venue }}",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "meeting_date": "Meeting date",
                    "meeting_time": "Meeting time",
                    "venue": "Meeting venue",
                },
            },
        ])

        # ==========================================
        # Security Alerts
        # ==========================================
        
        templates.extend([
            {
                "name": "new_device_login_email",
                "template_code": "NEW_DEVICE_LOGIN_EMAIL",
                "type": NotificationType.SECURITY_ALERT,
                "channel": NotificationChannel.EMAIL,
                "subject": "New Device Login Alert",
                "body": """Dear {{ member_name }},

We detected a login to your Digital Chama account from a new device.

Device: {{ device }}
Location: {{ location }}
Time: {{ login_time }}

If this was you, you can ignore this email.

If you didn't log in, please secure your account immediately by changing your password.

Best regards,
Digital Chama Security Team
""",
                "variables": {
                    "member_name": "Member full name",
                    "device": "Device information",
                    "location": "Login location",
                    "login_time": "Login timestamp",
                },
            },
            {
                "name": "new_device_login_sms",
                "template_code": "NEW_DEVICE_LOGIN_SMS",
                "type": NotificationType.SECURITY_ALERT,
                "channel": NotificationChannel.SMS,
                "subject": "",
                "body": "D-Chama: New login from {{ device }} in {{ location }}. If not you, change your password immediately.",
                "variables": {
                    "member_name": "Member full name",
                    "device": "Device information",
                    "location": "Login location",
                },
            },
            {
                "name": "password_changed_email",
                "template_code": "PASSWORD_CHANGED_EMAIL",
                "type": NotificationType.SECURITY_ALERT,
                "channel": NotificationChannel.EMAIL,
                "subject": "Password Changed Successfully",
                "body": """Dear {{ member_name }},

Your Digital Chama password was changed successfully.

If you did this, no action needed.

If you didn't change your password, please contact us immediately.

Best regards,
Digital Chama Security Team
""",
                "variables": {
                    "member_name": "Member full name",
                    "change_time": "Change timestamp",
                },
            },
        ])

        # ==========================================
        # Membership Templates
        # ==========================================
        
        templates.extend([
            {
                "name": "welcome_email",
                "template_code": "WELCOME_EMAIL",
                "type": NotificationType.SYSTEM,
                "channel": NotificationChannel.EMAIL,
                "subject": "Welcome to {{ chama_name }}",
                "body": """Dear {{ member_name }},

Welcome to {{ chama_name }}! We're excited to have you.

Your membership details:
- Member Number: {{ member_number }}
- Monthly Contribution: KES {{ monthly_contribution }}

Please review the chama constitution and payment schedule.

Questions? Contact us anytime.

Best regards,
{{ chama_name }} Management
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "member_number": "Member number",
                    "monthly_contribution": "Monthly contribution",
                },
            },
            {
                "name": "membership_approved_email",
                "template_code": "MEMBERSHIP_APPROVED_EMAIL",
                "type": NotificationType.MEMBERSHIP_UPDATE,
                "channel": NotificationChannel.EMAIL,
                "subject": "Membership Approved - {{ chama_name }}",
                "body": """Dear {{ member_name }},

Your membership application to {{ chama_name }} has been approved!

You are now a full member. Your member number is: {{ member_number }}

Please make your first contribution to activate your membership.

Welcome aboard!

Best regards,
{{ chama_name }} Management
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "member_number": "Member number",
                },
            },
        ])

        # ==========================================
        # Fine Templates
        # ==========================================
        
        templates.extend([
            {
                "name": "fine_issued_email",
                "template_code": "FINE_ISSUED_EMAIL",
                "type": NotificationType.FINE_UPDATE,
                "channel": NotificationChannel.EMAIL,
                "subject": "Fine Issued - {{ chama_name }}",
                "body": """Dear {{ member_name }},

A fine of KES {{ amount }} has been issued to you for: {{ reason }}

Due Date: {{ due_date }}

Please settle this promptly to avoid additional penalties.

Best regards,
{{ chama_name }} Management
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "amount": "Fine amount",
                    "reason": "Fine reason",
                    "due_date": "Due date",
                },
            },
        ])

        # ==========================================
        # Billing Templates
        # ==========================================
        
        templates.extend([
            {
                "name": "statement_ready_email",
                "template_code": "STATEMENT_READY_EMAIL",
                "type": NotificationType.BILLING_UPDATE,
                "channel": NotificationChannel.EMAIL,
                "subject": "Monthly Statement Ready - {{ chama_name }}",
                "body": """Dear {{ member_name }},

Your monthly statement for {{ period }} is now available.

Summary:
- Opening Balance: KES {{ opening_balance }}
- Contributions: KES {{ contributions }}
- Withdrawals: KES {{ withdrawals }}
- Loan Repayments: KES {{ loan_repayments }}
- Fines: KES {{ fines }}
- Closing Balance: KES {{ closing_balance }}

View full statement: {{ statement_link }}

Best regards,
{{ chama_name }} Treasurer
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "period": "Statement period",
                    "opening_balance": "Opening balance",
                    "contributions": "Total contributions",
                    "withdrawals": "Total withdrawals",
                    "loan_repayments": "Loan repayments",
                    "fines": "Total fines",
                    "closing_balance": "Closing balance",
                    "statement_link": "Statement URL",
                },
            },
        ])

        # ==========================================
        # General Announcements
        # ==========================================
        
        templates.extend([
            {
                "name": "general_announcement_email",
                "template_code": "GENERAL_ANNOUNCEMENT_EMAIL",
                "type": NotificationType.GENERAL_ANNOUNCEMENT,
                "channel": NotificationChannel.EMAIL,
                "subject": "{{ subject }}",
                "body": """Dear {{ member_name }},

{{ message }}

Best regards,
{{ chama_name }} Management
""",
                "variables": {
                    "member_name": "Member full name",
                    "chama_name": "Chama name",
                    "subject": "Announcement subject",
                    "message": "Announcement message",
                },
            },
        ])

        # Create templates
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for template_data in templates:
            channel = template_data.get("channel")
            if channel and channel not in channels:
                skipped_count += 1
                continue

            # Set channel from template data or default
            if not template_data.get("channel"):
                template_data["channel"] = NotificationChannel.EMAIL

            template, created = NotificationTemplate.objects.update_or_create(
                chama=None,
                name=template_data["name"],
                defaults=template_data,
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Created: {template.name} ({template.channel})")
                )
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding complete: {created_count} created, {updated_count} updated, {skipped_count} skipped"
            )
        )
