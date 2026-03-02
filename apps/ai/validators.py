"""
AI Validators and Safety Checks for Digital Chama System.

This module provides:
✅ Strict JSON schema validation
✅ Safety guardrails (no sensitive data leaks)
✅ Anti-hallucination checks
✅ Risk score validation
✅ Output sanitization
✅ Retry logic for invalid responses
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class AIValidationError(Exception):
    """Raised when AI output fails validation."""
    pass


class AISafetyViolation(Exception):
    """Raised when AI output contains unsafe content."""
    pass


# Strict JSON schemas for each context type
OUTPUT_SCHEMAS = {
    "membership_review": {
        "type": "object",
        "required": ["decision", "confidence", "risk_score", "reasons", "risk_flags", "questions_to_ask", "message_to_member", "next_steps_for_admin", "audit_summary"],
        "properties": {
            "decision": {"enum": ["APPROVE_RECOMMENDED", "REJECT_RECOMMENDED", "NEEDS_INFO", "INVALID_REQUEST"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "questions_to_ask": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "message_to_member": {"type": "string", "maxLength": 280},
            "next_steps_for_admin": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "audit_summary": {"type": "string"}
        }
    },
    
    "loan_eligibility": {
        "type": "object",
        "required": ["decision", "confidence", "risk_score", "reasons", "risk_flags", "questions_to_ask", "message_to_member", "next_steps_for_admin", "audit_summary"],
        "properties": {
            "decision": {"enum": ["APPROVE", "REDUCE_AMOUNT", "REQUIRE_GUARANTOR", "REJECT", "INVALID_REQUEST"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "questions_to_ask": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "message_to_member": {"type": "string", "maxLength": 280},
            "next_steps_for_admin": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "audit_summary": {"type": "string"}
        }
    },
    
    "withdrawal_review": {
        "type": "object",
        "required": ["decision", "confidence", "risk_score", "reasons", "risk_flags", "questions_to_ask", "message_to_member", "next_steps_for_admin", "audit_summary"],
        "properties": {
            "decision": {"enum": ["APPROVE_RECOMMENDED", "REQUIRE_SECOND_APPROVAL", "ESCALATE_REVIEW", "BLOCK", "INVALID_REQUEST"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "questions_to_ask": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "message_to_member": {"type": "string", "maxLength": 280},
            "next_steps_for_admin": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "audit_summary": {"type": "string"}
        }
    },
    
    "issue_triage": {
        "type": "object",
        "required": ["decision", "confidence", "risk_score", "reasons", "risk_flags", "questions_to_ask", "message_to_member", "next_steps_for_admin", "audit_summary"],
        "properties": {
            "decision": {"enum": ["LOW_PRIORITY", "MEDIUM_PRIORITY", "HIGH_PRIORITY", "CRITICAL", "INVALID_REQUEST"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "questions_to_ask": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "message_to_member": {"type": "string", "maxLength": 280},
            "next_steps_for_admin": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "audit_summary": {"type": "string"}
        }
    },
    
    "fraud_detection": {
        "type": "object",
        "required": ["decision", "confidence", "risk_score", "reasons", "risk_flags", "questions_to_ask", "message_to_member", "next_steps_for_admin", "audit_summary"],
        "properties": {
            "decision": {"enum": ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "ESCALATE_INVESTIGATION", "INVALID_REQUEST"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "questions_to_ask": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "message_to_member": {"type": "string", "maxLength": 280},
            "next_steps_for_admin": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "audit_summary": {"type": "string"}
        }
    },
    
    "loan_default_risk": {
        "type": "object",
        "required": ["decision", "confidence", "risk_score", "reasons", "risk_flags", "questions_to_ask", "message_to_member", "next_steps_for_admin", "audit_summary"],
        "properties": {
            "decision": {"enum": ["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "MONITOR_CLOSELY", "INVALID_REQUEST"]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "risk_flags": {"type": "array", "items": {"type": "string"}},
            "questions_to_ask": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "message_to_member": {"type": "string", "maxLength": 280},
            "next_steps_for_admin": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "audit_summary": {"type": "string"}
        }
    },
    
    "meeting_summarization": {
        "type": "object",
        "required": ["decisions", "action_items", "finance_discussion", "policy_changes", "next_meeting_date", "confidence", "audit_summary"],
        "properties": {
            "decisions": {"type": "array", "items": {"type": "string"}},
            "action_items": {"type": "array", "items": {"type": "string"}},
            "finance_discussion": {"type": "string"},
            "policy_changes": {"type": "array", "items": {"type": "string"}},
            "next_meeting_date": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "audit_summary": {"type": "string"}
        }
    },
    
    "report_explanation": {
        "type": "object",
        "required": ["key_metrics", "trends", "insights", "risk_warnings", "next_steps", "confidence", "audit_summary"],
        "properties": {
            "key_metrics": {"type": "array", "items": {"type": "string"}},
            "trends": {"type": "array", "items": {"type": "string"}},
            "insights": {"type": "array", "items": {"type": "string"}},
            "risk_warnings": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "audit_summary": {"type": "string"}
        }
    }
}


# Safety patterns that must NEVER appear in AI output
BANNED_PATTERNS = [
    r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',  # IP addresses
    r'\b\d{4}-\d{4}-\d{4}-\d{4}\b',  # Credit card numbers
    r'\b\d{10,15}\b',  # Long numbers (potential phone/account IDs)
    r'password|token|secret|key',  # Sensitive keywords
    r'internal|backend|system|admin',  # Internal references
    r'confidence.*score|risk.*formula',  # Internal scoring
]

# Allowed risk flags (prevents hallucination)
ALLOWED_RISK_FLAGS = {
    "DUPLICATE_ACTIVE_PHONE",
    "MULTIPLE_REQUESTS",
    "LARGE_AMOUNT_SPIKE",
    "RAPID_WITHDRAWALS",
    "UNVERIFIED_PHONE",
    "DEVICE_PATTERN_MATCH",
    "HIGH_DELINQUENCY_HISTORY",
    "REPEATED_FAILED_ATTEMPTS",
    "ACCOUNT_TOO_NEW",
    "INSUFFICIENT_HISTORY",
    "POLICY_VIOLATION",
    "AMOUNT_EXCEEDS_LIMIT",
    "FREQUENCY_TOO_HIGH",
    "BEHAVIORAL_ANOMALY",
    "LOCATION_MISMATCH",
}


class AIOutputValidator:
    """Validates AI output against strict schemas and safety rules."""
    
    @staticmethod
    def validate_json_structure(output: str, context_type: str) -> Dict[str, Any]:
        """
        Parse and validate JSON structure against schema.
        
        Raises:
            AIValidationError: If JSON is invalid or doesn't match schema
        """
        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            raise AIValidationError(f"Invalid JSON: {e}")
        
        schema = OUTPUT_SCHEMAS.get(context_type)
        if not schema:
            raise AIValidationError(f"Unknown context type: {context_type}")
        
        # Basic structure validation
        if not isinstance(data, dict):
            raise AIValidationError("Output must be a JSON object")
        
        # Check required fields
        required = schema.get("required", [])
        missing = [field for field in required if field not in data]
        if missing:
            raise AIValidationError(f"Missing required fields: {missing}")
        
        # Validate field types and constraints
        properties = schema.get("properties", {})
        for field, constraints in properties.items():
            if field in data:
                AIOutputValidator._validate_field(field, data[field], constraints)
        
        return data
    
    @staticmethod
    def _validate_field(field_name: str, value: Any, constraints: Dict[str, Any]) -> None:
        """Validate individual field against constraints."""
        field_type = constraints.get("type")
        
        # Type validation
        if field_type == "string" and not isinstance(value, str):
            raise AIValidationError(f"Field '{field_name}' must be string")
        elif field_type == "number" and not isinstance(value, (int, float)):
            raise AIValidationError(f"Field '{field_name}' must be number")
        elif field_type == "integer" and not isinstance(value, int):
            raise AIValidationError(f"Field '{field_name}' must be integer")
        elif field_type == "array" and not isinstance(value, list):
            raise AIValidationError(f"Field '{field_name}' must be array")
        
        # Enum validation
        if "enum" in constraints and value not in constraints["enum"]:
            raise AIValidationError(f"Field '{field_name}' must be one of: {constraints['enum']}")
        
        # Range validation
        if "minimum" in constraints and value < constraints["minimum"]:
            raise AIValidationError(f"Field '{field_name}' below minimum {constraints['minimum']}")
        if "maximum" in constraints and value > constraints["maximum"]:
            raise AIValidationError(f"Field '{field_name}' above maximum {constraints['maximum']}")
        
        # Length validation
        if "maxLength" in constraints and len(str(value)) > constraints["maxLength"]:
            raise AIValidationError(f"Field '{field_name}' exceeds max length {constraints['maxLength']}")
        if "maxItems" in constraints and isinstance(value, list) and len(value) > constraints["maxItems"]:
            raise AIValidationError(f"Field '{field_name}' exceeds max items {constraints['maxItems']}")
        
        # Array item validation
        if field_type == "array" and "items" in constraints:
            item_constraints = constraints["items"]
            for item in value:
                if item_constraints.get("type") == "string" and not isinstance(item, str):
                    raise AIValidationError(f"All items in '{field_name}' must be strings")
    
    @staticmethod
    def check_safety_violations(output: str) -> None:
        """
        Check for sensitive data leaks and banned content.
        
        Raises:
            AISafetyViolation: If unsafe content detected
        """
        for pattern in BANNED_PATTERNS:
            if re.search(pattern, output, re.IGNORECASE):
                raise AISafetyViolation(f"Safety violation detected: {pattern}")
    
    @staticmethod
    def validate_risk_flags(flags: List[str]) -> List[str]:
        """
        Validate and filter risk flags to prevent hallucination.
        
        Returns:
            List of validated flags (only allowed ones)
        """
        validated = []
        for flag in flags:
            if flag in ALLOWED_RISK_FLAGS:
                validated.append(flag)
            else:
                logger.warning(f"AI hallucinated invalid risk flag: {flag}")
        return validated
    
    @staticmethod
    def sanitize_member_message(message: str) -> str:
        """
        Sanitize member-facing messages to ensure they're safe and appropriate.
        
        Removes any potentially sensitive content and ensures friendly tone.
        """
        # Remove any technical jargon
        message = re.sub(r'\b(internal|backend|system|admin|confidence|risk)\b', '', message, flags=re.IGNORECASE)
        
        # Ensure message is not too technical
        if len(message) > 280:
            message = message[:277] + "..."
        
        # Basic safety check
        AIOutputValidator.check_safety_violations(message)
        
        return message.strip()
    
    @staticmethod
    def validate_business_rules_compliance(data: Dict[str, Any], context_type: str, system_rules: Dict[str, Any] = None) -> None:
        """
        Validate that AI decision complies with explicit business rules.
        
        This prevents AI from overriding hard constraints.
        """
        if not system_rules:
            return
        
        decision = data.get("decision")
        risk_score = data.get("risk_score", 0)
        
        # Hard rule: If phone_verified=false, cannot approve membership
        if context_type == "membership_review":
            phone_verified = system_rules.get("phone_verified", True)
            if not phone_verified and decision == "APPROVE_RECOMMENDED":
                raise AIValidationError("Cannot approve membership with unverified phone")
        
        # Hard rule: High risk scores must escalate
        if risk_score > 60 and decision not in ["ESCALATE_REVIEW", "REQUIRE_SECOND_APPROVAL", "BLOCK"]:
            raise AIValidationError("High risk score requires escalation")
        
        # Hard rule: Withdrawal limits
        if context_type == "withdrawal_review":
            max_daily = system_rules.get("max_daily_withdrawal", 0)
            requested_amount = system_rules.get("requested_amount", 0)
            if requested_amount > max_daily and decision == "APPROVE_RECOMMENDED":
                raise AIValidationError("Cannot approve withdrawal exceeding daily limit")


class AIRetryHandler:
    """Handles AI response retries with fallback logic."""
    
    @staticmethod
    def retry_on_validation_error(
        ai_call_func, 
        context_type: str, 
        max_retries: int = 1,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Retry AI call if validation fails, with corrective prompt.
        
        Args:
            ai_call_func: Function that makes AI call and returns raw output
            context_type: Type of AI context (for schema validation)
            max_retries: Maximum retry attempts
            **kwargs: Arguments to pass to ai_call_func
        
        Returns:
            Validated AI response data
        
        Raises:
            AIValidationError: If all retries fail
        """
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                # Make AI call
                raw_output = ai_call_func(**kwargs)
                
                # Validate output
                validated_data = AIOutputValidator.validate_json_structure(raw_output, context_type)
                
                # Check safety
                AIOutputValidator.check_safety_violations(raw_output)
                
                # Validate risk flags
                if "risk_flags" in validated_data:
                    validated_data["risk_flags"] = AIOutputValidator.validate_risk_flags(
                        validated_data["risk_flags"]
                    )
                
                # Sanitize member message
                if "message_to_member" in validated_data:
                    validated_data["message_to_member"] = AIOutputValidator.sanitize_member_message(
                        validated_data["message_to_member"]
                    )
                
                # Check business rules compliance
                system_rules = kwargs.get("system_rules", {})
                AIOutputValidator.validate_business_rules_compliance(
                    validated_data, context_type, system_rules
                )
                
                return validated_data
                
            except (AIValidationError, AISafetyViolation, json.JSONDecodeError) as e:
                last_error = e
                logger.warning(f"AI validation failed (attempt {attempt + 1}): {e}")
                
                if attempt < max_retries:
                    # Add corrective instruction for retry
                    if "corrective_prompt" in kwargs:
                        kwargs["corrective_prompt"] += f"\n\nPREVIOUS ERROR: {e}\nPlease fix and return valid JSON."
                    else:
                        kwargs["corrective_prompt"] = f"ERROR: {e}\nReturn valid JSON matching the schema."
                else:
                    break
        
        # All retries failed
        raise AIValidationError(f"AI validation failed after {max_retries + 1} attempts: {last_error}")


def create_safe_fallback_response(context_type: str, error_reason: str) -> Dict[str, Any]:
    """
    Create a safe fallback response when AI fails completely.
    
    This ensures the system never breaks due to AI issues.
    """
    base_response = {
        "decision": "NEEDS_INFO",
        "confidence": 0.0,
        "risk_score": 50,
        "reasons": [f"AI processing failed: {error_reason}", "Manual review recommended"],
        "risk_flags": ["AI_FAILURE"],
        "questions_to_ask": ["Please try again later"],
        "message_to_member": "We're experiencing technical difficulties. Please contact support.",
        "next_steps_for_admin": ["Review manually", "Check AI system status"],
        "audit_summary": f"AI failed: {error_reason} - manual review required"
    }
    
    # Adjust for context-specific schemas
    if context_type == "meeting_summarization":
        return {
            "decisions": [],
            "action_items": ["Manual review required"],
            "finance_discussion": "AI processing failed",
            "policy_changes": [],
            "next_meeting_date": "Unknown",
            "confidence": 0.0,
            "audit_summary": f"AI failed: {error_reason}"
        }
    elif context_type == "report_explanation":
        return {
            "key_metrics": ["AI processing failed"],
            "trends": [],
            "insights": ["Manual review required"],
            "risk_warnings": [error_reason],
            "next_steps": ["Contact support"],
            "confidence": 0.0,
            "audit_summary": f"AI failed: {error_reason}"
        }
    
    return base_response