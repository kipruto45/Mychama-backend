"""
Centralized Feedback and Messaging System

Provides consistent, user-friendly feedback for all API responses across
the application. Each error/warning/success code maps to:
- A user-friendly message
- A technical code for logging
- Whether it's retryable
- Related HTTP status code
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FeedbackScope(str, Enum):
    """Scope/context where feedback applies."""
    AUTH = "auth"
    CHAMA = "chama"
    KYC = "kyc"
    FINANCE = "finance"
    LOAN = "loan"
    PAYMENT = "payment"
    PROFILE = "profile"
    GENERAL = "general"


@dataclass(frozen=True)
class FeedbackCode:
    """Feedback code definition."""
    code: str
    message: str
    scope: FeedbackScope
    status_code: int
    retryable: bool = False
    field: str | None = None
    action: str | None = None  # What user should do next


AUTH_FEEDBACK = {
    # Registration
    "REGISTER_SUCCESS": FeedbackCode(
        code="REGISTER_SUCCESS",
        message="Account created successfully. Please verify your email.",
        scope=FeedbackScope.AUTH,
        status_code=201,
    ),
    "REGISTER_SUCCESS_OTP_PENDING": FeedbackCode(
        code="REGISTER_SUCCESS_OTP_PENDING",
        message="Your account has been created. Please verify your phone number to continue.",
        scope=FeedbackScope.AUTH,
        status_code=201,
    ),
    "REGISTER_EMAIL_EXISTS": FeedbackCode(
        code="REGISTER_EMAIL_EXISTS",
        message="An account with this email already exists.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="email",
    ),
    "REGISTER_PHONE_EXISTS": FeedbackCode(
        code="REGISTER_PHONE_EXISTS",
        message="An account with this phone number already exists.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="phone",
    ),
    "REGISTER_WEAK_PASSWORD": FeedbackCode(
        code="REGISTER_WEAK_PASSWORD",
        message="Your password is too weak. Use at least 8 characters with uppercase, lowercase, number, and special character.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="password",
    ),
    "REGISTER_PASSWORD_MISMATCH": FeedbackCode(
        code="REGISTER_PASSWORD_MISMATCH",
        message="Passwords do not match.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="password_confirm",
    ),
    "REGISTER_INVALID_PHONE": FeedbackCode(
        code="REGISTER_INVALID_PHONE",
        message="Enter a valid phone number (e.g., 0722123456).",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="phone",
    ),
    "REGISTER_INVALID_EMAIL": FeedbackCode(
        code="REGISTER_INVALID_EMAIL",
        message="Enter a valid email address.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="email",
    ),
    "REGISTER_MISSING_FIELD": FeedbackCode(
        code="REGISTER_MISSING_FIELD",
        message="Please fill in all required fields.",
        scope=FeedbackScope.AUTH,
        status_code=400,
    ),

    # Login
    "LOGIN_SUCCESS": FeedbackCode(
        code="LOGIN_SUCCESS",
        message="Login successful. Welcome back!",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),
    "LOGIN_FAILED": FeedbackCode(
        code="LOGIN_FAILED",
        message="Invalid phone number or password.",
        scope=FeedbackScope.AUTH,
        status_code=401,
        field="password",
    ),
    "LOGIN_ACCOUNT_LOCKED": FeedbackCode(
        code="LOGIN_ACCOUNT_LOCKED",
        message="Too many failed attempts. Please wait 15 minutes before trying again.",
        scope=FeedbackScope.AUTH,
        status_code=429,
        retryable=True,
    ),
    "LOGIN_ACCOUNT_INACTIVE": FeedbackCode(
        code="LOGIN_ACCOUNT_INACTIVE",
        message="Your account has been deactivated. Contact support for help.",
        scope=FeedbackScope.AUTH,
        status_code=403,
    ),

    # OTP
    "OTP_SENT": FeedbackCode(
        code="OTP_SENT",
        message="Verification code sent successfully.",
        scope=FeedbackScope.AUTH,
        status_code=200,
        action="check_phone",
    ),
    "OTP_SENT_EMAIL": FeedbackCode(
        code="OTP_SENT_EMAIL",
        message="Verification code sent to your email.",
        scope=FeedbackScope.AUTH,
        status_code=200,
        action="check_email",
    ),
    "OTP_INVALID": FeedbackCode(
        code="OTP_INVALID",
        message="The verification code you entered is invalid.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="code",
    ),
    "OTP_EXPIRED": FeedbackCode(
        code="OTP_EXPIRED",
        message="This verification code has expired. Request a new one.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="code",
        action="resend_code",
    ),
    "OTP_REQUIRED": FeedbackCode(
        code="OTP_REQUIRED",
        message="Please enter the verification code sent to your phone.",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),
    "OTP_RATE_LIMITED": FeedbackCode(
        code="OTP_RATE_LIMITED",
        message="Too many requests. Please wait a moment and try again.",
        scope=FeedbackScope.AUTH,
        status_code=429,
        retryable=True,
    ),
    "OTP_DELIVERY_FAILED": FeedbackCode(
        code="OTP_DELIVERY_FAILED",
        message="We could not send the verification code right now. Please try again shortly.",
        scope=FeedbackScope.AUTH,
        status_code=503,
        retryable=True,
    ),

    # Logout
    "LOGOUT_SUCCESS": FeedbackCode(
        code="LOGOUT_SUCCESS",
        message="You have been logged out successfully.",
        scope=FeedbackScope.AUTH,
        status_code=205,
    ),

    # Password Reset
    "PASSWORD_RESET_SENT": FeedbackCode(
        code="PASSWORD_RESET_SENT",
        message="Password reset link sent to your email.",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),
    "PASSWORD_RESET_SUCCESS": FeedbackCode(
        code="PASSWORD_RESET_SUCCESS",
        message="Password reset successfully. You can now sign in with your new password.",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),
    "PASSWORD_RESET_INVALID_TOKEN": FeedbackCode(
        code="PASSWORD_RESET_INVALID_TOKEN",
        message="This reset link is invalid or has expired. Request a new one.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        action="request_reset",
    ),
    "PASSWORD_RESET_WEAK_PASSWORD": FeedbackCode(
        code="PASSWORD_RESET_WEAK_PASSWORD",
        message="Your new password is too weak. Use at least 8 characters with uppercase, lowercase, number, and special character.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="new_password",
    ),
    "PASSWORD_CHANGE_SUCCESS": FeedbackCode(
        code="PASSWORD_CHANGE_SUCCESS",
        message="Password changed successfully.",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),
    "PASSWORD_CHANGE_WRONG_OLD": FeedbackCode(
        code="PASSWORD_CHANGE_WRONG_OLD",
        message="Your current password is incorrect.",
        scope=FeedbackScope.AUTH,
        status_code=400,
        field="old_password",
    ),

    # Verification
    "VERIFICATION_SUCCESS": FeedbackCode(
        code="VERIFICATION_SUCCESS",
        message="Phone number verified successfully.",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),
    "VERIFICATION_ALREADY_DONE": FeedbackCode(
        code="VERIFICATION_ALREADY_DONE",
        message="This phone number is already verified.",
        scope=FeedbackScope.AUTH,
        status_code=200,
    ),

    # Session
    "SESSION_EXPIRED": FeedbackCode(
        code="SESSION_EXPIRED",
        message="Your session has expired. Please sign in again.",
        scope=FeedbackScope.AUTH,
        status_code=401,
    ),
    "TOKEN_INVALID": FeedbackCode(
        code="TOKEN_INVALID",
        message="Please sign in again to continue.",
        scope=FeedbackScope.AUTH,
        status_code=401,
    ),
}

CHAMA_FEEDBACK = {
    "CHAMA_CREATE_SUCCESS": FeedbackCode(
        code="CHAMA_CREATE_SUCCESS",
        message="Chama created successfully!",
        scope=FeedbackScope.CHAMA,
        status_code=201,
    ),
    "CHAMA_JOIN_SUCCESS": FeedbackCode(
        code="CHAMA_JOIN_SUCCESS",
        message="You have joined the chama successfully!",
        scope=FeedbackScope.CHAMA,
        status_code=200,
    ),
    "CHAMA_JOIN_REQUEST_PENDING": FeedbackCode(
        code="CHAMA_JOIN_REQUEST_PENDING",
        message="Your join request is awaiting approval from the chama admin.",
        scope=FeedbackScope.CHAMA,
        status_code=200,
    ),
    "CHAMA_ALREADY_MEMBER": FeedbackCode(
        code="CHAMA_ALREADY_MEMBER",
        message="You are already a member of this chama.",
        scope=FeedbackScope.CHAMA,
        status_code=400,
    ),
    "CHAMA_NOT_FOUND": FeedbackCode(
        code="CHAMA_NOT_FOUND",
        message="The chama could not be found.",
        scope=FeedbackScope.CHAMA,
        status_code=404,
    ),
    "CHAMA_INACTIVE": FeedbackCode(
        code="CHAMA_INACTIVE",
        message="This chama is not accepting new members right now.",
        scope=FeedbackScope.CHAMA,
        status_code=400,
    ),
    "CHAMA_MEMBERSHIP_FULL": FeedbackCode(
        code="CHAMA_MEMBERSHIP_FULL",
        message="This chama has reached its member limit.",
        scope=FeedbackScope.CHAMA,
        status_code=400,
    ),
    "CHAMA_INVITE_INVALID": FeedbackCode(
        code="CHAMA_INVITE_INVALID",
        message="This invitation is no longer valid.",
        scope=FeedbackScope.CHAMA,
        status_code=404,
    ),
    "CHAMA_INVITE_EXPIRED": FeedbackCode(
        code="CHAMA_INVITE_EXPIRED",
        message="This invitation has expired.",
        scope=FeedbackScope.CHAMA,
        status_code=404,
    ),
    "CHAMA_LEFT_SUCCESS": FeedbackCode(
        code="CHAMA_LEFT_SUCCESS",
        message="You have left the chama.",
        scope=FeedbackScope.CHAMA,
        status_code=200,
    ),
}

KYC_FEEDBACK = {
    "KYC_SUBMITTED": FeedbackCode(
        code="KYC_SUBMITTED",
        message="Your KYC documents have been submitted for review.",
        scope=FeedbackScope.KYC,
        status_code=200,
    ),
    "KYC_APPROVED": FeedbackCode(
        code="KYC_APPROVED",
        message="Your identity has been verified successfully.",
        scope=FeedbackScope.KYC,
        status_code=200,
    ),
    "KYC_REJECTED": FeedbackCode(
        code="KYC_REJECTED",
        message="Your KYC documents were rejected. Please submit clearer documents.",
        scope=FeedbackScope.KYC,
        status_code=400,
    ),
    "KYC_PENDING": FeedbackCode(
        code="KYC_PENDING",
        message="Your documents are under review.",
        scope=FeedbackScope.KYC,
        status_code=200,
    ),
    "KYC_MISSING_DOCUMENT": FeedbackCode(
        code="KYC_MISSING_DOCUMENT",
        message="Please upload all required documents.",
        scope=FeedbackScope.KYC,
        status_code=400,
    ),
    "KYC_INVALID_DOCUMENT": FeedbackCode(
        code="KYC_INVALID_DOCUMENT",
        message="The uploaded document is invalid or unreadable.",
        scope=FeedbackScope.KYC,
        status_code=400,
    ),
    "KYC_FILE_TOO_LARGE": FeedbackCode(
        code="KYC_FILE_TOO_LARGE",
        message="File is too large. Please upload a smaller image (max 5MB).",
        scope=FeedbackScope.KYC,
        status_code=400,
    ),
    "KYC_UNSUPPORTED_FILE_TYPE": FeedbackCode(
        code="KYC_UNSUPPORTED_FILE_TYPE",
        message="This file type is not supported. Use JPG or PNG.",
        scope=FeedbackScope.KYC,
        status_code=400,
    ),
}

FINANCE_FEEDBACK = {
    "CONTRIBUTION_SUCCESS": FeedbackCode(
        code="CONTRIBUTION_SUCCESS",
        message="Contribution submitted successfully!",
        scope=FeedbackScope.FINANCE,
        status_code=200,
    ),
    "CONTRIBUTION_FAILED": FeedbackCode(
        code="CONTRIBUTION_FAILED",
        message="Your contribution could not be processed. Please try again.",
        scope=FeedbackScope.FINANCE,
        status_code=400,
    ),
    "CONTRIBUTION_INVALID_AMOUNT": FeedbackCode(
        code="CONTRIBUTION_INVALID_AMOUNT",
        message="Enter a valid amount greater than zero.",
        scope=FeedbackScope.FINANCE,
        status_code=400,
        field="amount",
    ),
    "WITHDRAWAL_SUCCESS": FeedbackCode(
        code="WITHDRAWAL_SUCCESS",
        message="Withdrawal request submitted successfully!",
        scope=FeedbackScope.FINANCE,
        status_code=200,
    ),
    "WITHDRAWAL_PENDING": FeedbackCode(
        code="WITHDRAWAL_PENDING",
        message="Your withdrawal is pending approval.",
        scope=FeedbackScope.FINANCE,
        status_code=200,
    ),
    "WITHDRAWAL_INSUFFICIENT_BALANCE": FeedbackCode(
        code="WITHDRAWAL_INSUFFICIENT_BALANCE",
        message="You do not have enough balance to complete this withdrawal.",
        scope=FeedbackScope.FINANCE,
        status_code=400,
        field="amount",
    ),
    "WALLET_FUNDING_SUCCESS": FeedbackCode(
        code="WALLET_FUNDING_SUCCESS",
        message="Wallet funded successfully!",
        scope=FeedbackScope.FINANCE,
        status_code=200,
    ),
    "WALLET_BALANCE_UPDATED": FeedbackCode(
        code="WALLET_BALANCE_UPDATED",
        message="Your wallet balance has been updated.",
        scope=FeedbackScope.FINANCE,
        status_code=200,
    ),
}

LOAN_FEEDBACK = {
    "LOAN_APPLICATION_SUCCESS": FeedbackCode(
        code="LOAN_APPLICATION_SUCCESS",
        message="Loan application received and is under review.",
        scope=FeedbackScope.LOAN,
        status_code=201,
    ),
    "LOAN_APPROVED": FeedbackCode(
        code="LOAN_APPROVED",
        message="Your loan has been approved!",
        scope=FeedbackScope.LOAN,
        status_code=200,
    ),
    "LOAN_REJECTED": FeedbackCode(
        code="LOAN_REJECTED",
        message="Your loan application was not approved at this time.",
        scope=FeedbackScope.LOAN,
        status_code=400,
    ),
    "LOAN_INELIGIBLE": FeedbackCode(
        code="LOAN_INELIGIBLE",
        message="You are not eligible to apply for a loan at this time.",
        scope=FeedbackScope.LOAN,
        status_code=400,
    ),
    "LOAN_ALREADY_APPLIED": FeedbackCode(
        code="LOAN_ALREADY_APPLIED",
        message="You already have a pending loan application.",
        scope=FeedbackScope.LOAN,
        status_code=400,
    ),
    "LOAN_AMOUNT_TOO_HIGH": FeedbackCode(
        code="LOAN_AMOUNT_TOO_HIGH",
        message="The requested amount exceeds the maximum allowed.",
        scope=FeedbackScope.LOAN,
        status_code=400,
        field="amount",
    ),
    "LOAN_AMOUNT_TOO_LOW": FeedbackCode(
        code="LOAN_AMOUNT_TOO_LOW",
        message="The requested amount is below the minimum allowed.",
        scope=FeedbackScope.LOAN,
        status_code=400,
        field="amount",
    ),
    "LOAN_DUPLICATE": FeedbackCode(
        code="LOAN_DUPLICATE",
        message="This loan has already been applied for.",
        scope=FeedbackScope.LOAN,
        status_code=400,
    ),
    "LOAN_GUARANTOR_REQUIRED": FeedbackCode(
        code="LOAN_GUARANTOR_REQUIRED",
        message="Please select the required guarantors.",
        scope=FeedbackScope.LOAN,
        status_code=400,
    ),
    "LOAN_GUARANTOR_INSUFFICIENT": FeedbackCode(
        code="LOAN_GUARANTOR_INSUFFICIENT",
        message="Selected guarantors do not have enough capacity.",
        scope=FeedbackScope.LOAN,
        status_code=400,
    ),
}

PROFILE_FEEDBACK = {
    "PROFILE_UPDATE_SUCCESS": FeedbackCode(
        code="PROFILE_UPDATE_SUCCESS",
        message="Profile updated successfully!",
        scope=FeedbackScope.PROFILE,
        status_code=200,
    ),
    "PROFILE_EMAIL_TAKEN": FeedbackCode(
        code="PROFILE_EMAIL_TAKEN",
        message="This email is already in use by another account.",
        scope=FeedbackScope.PROFILE,
        status_code=400,
        field="email",
    ),
}

GENERAL_FEEDBACK = {
    # System errors
    "SUCCESS": FeedbackCode(
        code="SUCCESS",
        message="Success!",
        scope=FeedbackScope.GENERAL,
        status_code=200,
    ),
    "VALIDATION_ERROR": FeedbackCode(
        code="VALIDATION_ERROR",
        message="Please check your input and try again.",
        scope=FeedbackScope.GENERAL,
        status_code=400,
    ),
    "UNAUTHORIZED": FeedbackCode(
        code="UNAUTHORIZED",
        message="Please sign in to continue.",
        scope=FeedbackScope.GENERAL,
        status_code=401,
    ),
    "FORBIDDEN": FeedbackCode(
        code="FORBIDDEN",
        message="You do not have permission to perform this action.",
        scope=FeedbackScope.GENERAL,
        status_code=403,
    ),
    "NOT_FOUND": FeedbackCode(
        code="NOT_FOUND",
        message="The requested item could not be found.",
        scope=FeedbackScope.GENERAL,
        status_code=404,
    ),
    "CONFLICT": FeedbackCode(
        code="CONFLICT",
        message="This action conflicts with the current state.",
        scope=FeedbackScope.GENERAL,
        status_code=409,
    ),
    "RATE_LIMITED": FeedbackCode(
        code="RATE_LIMITED",
        message="Too many requests. Please wait and try again.",
        scope=FeedbackScope.GENERAL,
        status_code=429,
        retryable=True,
    ),
    "SERVER_ERROR": FeedbackCode(
        code="SERVER_ERROR",
        message="Something went wrong on our side. Please try again later.",
        scope=FeedbackScope.GENERAL,
        status_code=500,
        retryable=True,
    ),
    "NETWORK_ERROR": FeedbackCode(
        code="NETWORK_ERROR",
        message="Unable to connect. Check your internet connection and try again.",
        scope=FeedbackScope.GENERAL,
        status_code=0,
        retryable=True,
    ),
    "TIMEOUT": FeedbackCode(
        code="TIMEOUT",
        message="The request took too long. Please try again.",
        scope=FeedbackScope.GENERAL,
        status_code=0,
        retryable=True,
    ),
    "UNKNOWN_ERROR": FeedbackCode(
        code="UNKNOWN_ERROR",
        message="An unexpected error occurred. Please try again.",
        scope=FeedbackScope.GENERAL,
        status_code=500,
        retryable=True,
    ),
}


ALL_FEEDBACK = (
    AUTH_FEEDBACK
    | CHAMA_FEEDBACK
    | KYC_FEEDBACK
    | FINANCE_FEEDBACK
    | LOAN_FEEDBACK
    | PROFILE_FEEDBACK
    | GENERAL_FEEDBACK
)


def get_feedback(code: str) -> FeedbackCode | None:
    """Get feedback by code."""
    return ALL_FEEDBACK.get(code)


def get_feedback_message(code: str, fallback: str | None = None) -> str:
    """Get user-friendly message for a code."""
    feedback = ALL_FEEDBACK.get(code)
    if feedback:
        return feedback.message
    return fallback or "An unexpected error occurred. Please try again."


def get_feedback_status(code: str) -> int:
    """Get HTTP status code for a feedback code."""
    feedback = ALL_FEEDBACK.get(code)
    if feedback:
        return feedback.status_code
    return 500


def build_feedback_response(
    code: str,
    data: Any = None,
    details: dict | None = None,
) -> dict:
    """Build a standardized API response with feedback."""
    feedback = ALL_FEEDBACK.get(code)
    
    if feedback:
        response = {
            "success": feedback.status_code < 400,
            "code": code,
            "message": feedback.message,
        }
        if data is not None:
            response["data"] = data
        if details:
            response["details"] = details
        return response
    
    # Fallback for unknown codes
    return {
        "success": False,
        "code": code,
        "message": get_feedback_message(code),
        "details": details or {},
    }


def build_error_response(
    code: str,
    field: str | None = None,
    details: dict | None = None,
) -> dict:
    """Build a standardized error response."""
    feedback = ALL_FEEDBACK.get(code)
    
    if feedback:
        errors = details or {}
        if field:
            errors[field] = [feedback.message]
        
        return {
            "success": False,
            "code": code,
            "message": feedback.message,
            "errors": errors,
        }
    
    # Fallback
    return {
        "success": False,
        "code": code,
        "message": get_feedback_message(code),
        "errors": details or {},
    }


def build_success_response(
    code: str,
    data: Any = None,
    message: str | None = None,
) -> dict:
    """Build a standardized success response."""
    feedback = ALL_FEEDBACK.get(code)
    
    response = {
        "success": True,
        "code": code,
        "message": message or (feedback.message if feedback else "Success!"),
    }
    if data is not None:
        response["data"] = data
    return response