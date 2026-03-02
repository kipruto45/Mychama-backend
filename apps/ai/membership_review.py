"""
AI-Membership Integration Service for Digital Chama.

This module provides the integration between the new MembershipRequest model
and the existing AI system for intelligent membership review.

Key Features:
- Rule-first decision making (AI only decides when rules are ambiguous)
- Structured JSON output enforcement
- Async processing with Celery
- Caching for performance
- Full audit trail
"""

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import User
from apps.ai.prompts import (
    FAST_TASKS,
    get_model_for_task,
    get_token_limit,
)
from apps.ai.rag_engine import get_ai_context_for_decision
from apps.ai.validators import (
    AIOutputValidator,
    AIRetryHandler,
    create_safe_fallback_response,
)
from apps.chama.models import (
    Chama,
    Membership,
    MembershipRequest,
    MembershipRequestStatus,
    MemberStatus,
    MembershipRole,
)

logger = logging.getLogger(__name__)


class MembershipAIReviewService:
    """
    Service for AI-powered membership review with rule-first approach.
    
    Decision hierarchy:
    1. If phone_verified=false → automatic NEEDS_INFO (no AI needed)
    2. If duplicate active membership → automatic REJECT
    3. If existing pending request → automatic NEEDS_INFO
    4. Otherwise → AI review for recommendation
    """
    
    # Hard rules that don't need AI
    HARD_RULE_CHECKS = [
        ("phone_verified", "check_phone_verified"),
        ("duplicate_active", "check_duplicate_active"),
        ("pending_exists", "check_pending_exists"),
        ("chama_active", "check_chama_active"),
    ]
    
    @classmethod
    def get_membership_rules(cls, chama: Chama) -> Dict[str, Any]:
        """Get business rules for this chama."""
        return {
            "approval_required": chama.require_approval,
            "requires_phone_verified": True,  # HARD REQUIREMENT
            "max_members": chama.max_members,
            "allow_public_join": chama.allow_public_join,
        }
    
    @classmethod
    def get_policy_limits(cls, chama: Chama) -> Dict[str, Any]:
        """Get policy limits for this chama."""
        return {
            "max_daily_join_requests": 3,
            "require_kyc": False,  # Future feature
        }
    
    @classmethod
    def build_request_data(cls, membership_request: MembershipRequest) -> Dict[str, Any]:
        """Build request data for AI from MembershipRequest."""
        user = membership_request.user
        
        # Calculate account age
        account_age_days = (timezone.now() - user.date_joined).days if user.date_joined else 0
        
        # Get request count in last 24 hours
        from apps.chama.models import MembershipRequest
        requests_last_24h = MembershipRequest.objects.filter(
            user=user,
            created_at__gte=timezone.now() - timedelta(hours=24)
        ).count()
        
        return {
            "member_name": user.full_name or "Unknown",
            "member_phone": user.phone[-4:].rjust(len(user.phone), "*"),  # Masked
            "phone_verified": user.phone_verified,
            "account_age_days": account_age_days,
            "email_verified": bool(user.email),
            "requests_last_24h": requests_last_24h,
            "request_note": membership_request.request_note or "",
            "ip_address": membership_request.ip_address,  # Don't send full IP
            "device_info": membership_request.device_info[:50] if membership_request.device_info else "",
        }
    
    @classmethod
    def build_risk_signals(cls, membership_request: MembershipRequest, chama: Chama) -> Dict[str, Any]:
        """Build risk signals for AI from MembershipRequest."""
        user = membership_request.user
        
        # Check for duplicate active phone
        duplicate_active = Membership.objects.filter(
            user__phone=user.phone,
            status=MemberStatus.ACTIVE,
            is_active=True
        ).exclude(chama=chama).exists()
        
        return {
            "duplicate_phone_active": duplicate_active,
            "requests_last_24h": membership_request.created_at >= timezone.now() - timedelta(hours=24),
            "device_known": bool(membership_request.device_info),
            "ip_provided": bool(membership_request.ip_address),
        }
    
    @classmethod
    def check_hard_rules(cls, membership_request: MembershipRequest, chama: Chama) -> Optional[Dict[str, Any]]:
        """
        Check hard business rules that don't need AI.
        
        Returns:
            None if all rules pass (proceed to AI)
            Dict with decision if rules decide outcome
        """
        user = membership_request.user
        
        # Rule 1: Phone must be verified
        if not user.phone_verified:
            logger.info(f"Membership request {membership_request.id}: blocked - phone not verified")
            return {
                "decision": "NEEDS_INFO",
                "confidence": 1.0,
                "risk_score": 0,
                "reasons": ["Phone number must be verified before membership approval"],
                "risk_flags": ["UNVERIFIED_PHONE"],
                "questions_to_ask": ["Please verify your phone number first"],
                "message_to_member": "Please verify your phone number to complete your membership request.",
                "next_steps_for_admin": ["Wait for phone verification", "Member can verify in profile settings"],
                "audit_summary": "Auto-blocked: phone not verified",
                "ai_recommended": False,
            }
        
        # Rule 2: Check for duplicate active membership
        duplicate = Membership.objects.filter(
            user=user,
            chama=chama,
            status=MemberStatus.ACTIVE,
            is_active=True
        ).exists()
        
        if duplicate:
            logger.info(f"Membership request {membership_request.id}: blocked - duplicate active membership")
            return {
                "decision": "REJECT_RECOMMENDED",
                "confidence": 1.0,
                "risk_score": 100,
                "reasons": ["User already has active membership in this chama"],
                "risk_flags": ["DUPLICATE_ACTIVE_PHONE"],
                "questions_to_ask": [],
                "message_to_member": "You are already a member of this chama.",
                "next_steps_for_admin": ["Close duplicate request"],
                "audit_summary": "Auto-rejected: duplicate active membership",
                "ai_recommended": False,
            }
        
        # Rule 3: Check for existing pending request
        existing_pending = MembershipRequest.objects.filter(
            user=user,
            chama=chama,
            status=MembershipRequestStatus.PENDING
        ).exclude(id=membership_request.id).exists()
        
        if existing_pending:
            logger.info(f"Membership request {membership_request.id}: blocked - existing pending request")
            return {
                "decision": "NEEDS_INFO",
                "confidence": 1.0,
                "risk_score": 0,
                "reasons": ["A pending request already exists for this user"],
                "risk_flags": ["MULTIPLE_REQUESTS"],
                "questions_to_ask": [],
                "message_to_member": "You already have a pending request for this chama.",
                "next_steps_for_admin": ["Review existing request first"],
                "audit_summary": "Auto-needs-info: existing pending request",
                "ai_recommended": False,
            }
        
        # Rule 4: Chama must be active
        if chama.status != "active":
            logger.info(f"Membership request {membership_request.id}: blocked - chama not active")
            return {
                "decision": "REJECT_RECOMMENDED",
                "confidence": 1.0,
                "risk_score": 100,
                "reasons": ["Chama is not active"],
                "risk_flags": ["CHAMA_INACTIVE"],
                "questions_to_ask": [],
                "message_to_member": "This chama is currently not accepting new members.",
                "next_steps_for_admin": ["Reactivate chama first"],
                "audit_summary": "Auto-rejected: chama inactive",
                "ai_recommended": False,
            }
        
        # All hard rules pass - proceed to AI
        return None
    
    @classmethod
    async def get_ai_recommendation(cls, membership_request: MembershipRequest) -> Dict[str, Any]:
        """
        Get AI recommendation for membership request.
        
        This is the main entry point for AI membership review.
        """
        # Check hard rules first
        chama = membership_request.chama
        hard_rule_result = cls.check_hard_rules(membership_request, chama)
        
        if hard_rule_result:
            return hard_rule_result
        
        # Build context for AI
        system_rules = cls.get_membership_rules(chama)
        policy_limits = cls.get_policy_limits(chama)
        request_data = cls.build_request_data(membership_request)
        risk_signals = cls.build_risk_signals(membership_request, chama)
        
        # Get RAG context (policies, rules)
        context_data = {
            "context_type": "membership_review",
            "chama_id": str(chama.id),
            "request_data": request_data,
            "risk_signals": risk_signals,
            "system_rules": system_rules,
            "policy_limits": policy_limits,
        }
        
        # Get AI context from RAG
        rag_context = get_ai_context_for_decision(
            context_type="membership_review",
            chama_id=str(chama.id),
            request_data=request_data,
            risk_signals=risk_signals,
            system_rules=system_rules,
            policy_limits=policy_limits,
        )
        
        # Check cache first
        cache_key = f"ai_membership_review:{membership_request.id}"
        cached = cache.get(cache_key)
        if cached:
            logger.info(f"Cache hit for membership request {membership_request.id}")
            return cached
        
        # Make AI call
        try:
            from apps.ai.services import AIService
            
            # Use fast model for membership review (it's a classification task)
            model = get_model_for_task("membership_review")
            max_tokens = get_token_limit("membership_review")
            
            result = await AIService.process_decision_request(
                context_type="membership_review",
                chama_id=str(chama.id),
                request_data=request_data,
                risk_signals=risk_signals,
                system_rules=system_rules,
                policy_limits=policy_limits,
                additional_context=rag_context,
                model=model,
                max_tokens=max_tokens,
            )
            
            # Validate output
            validated = AIOutputValidator.validate_json_structure(
                json.dumps(result), 
                "membership_review"
            )
            
            # Add metadata
            validated["ai_recommended"] = True
            validated["model_used"] = model
            
            # Cache result
            cache.set(cache_key, validated, timeout=3600)  # 1 hour
            
            return validated
            
        except Exception as e:
            logger.error(f"AI membership review failed: {e}")
            return create_safe_fallback_response("membership_review", str(e))


@shared_task
def process_membership_ai_review(membership_request_id: str):
    """
    Celery task for async AI membership review.
    
    Run this as background job when membership request is created.
    """
    try:
        request = MembershipRequest.objects.get(id=membership_request_id)
        
        # Get synchronous AI recommendation
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                MembershipAIReviewService.get_ai_recommendation(request)
            )
        finally:
            loop.close()
        
        # Store result in membership request
        request.ai_decision = result.get("decision")
        request.ai_confidence = result.get("confidence")
        request.ai_risk_score = result.get("risk_score")
        request.ai_recommendation = json.dumps(result)
        request.save(update_fields=[
            "ai_decision",
            "ai_confidence", 
            "ai_risk_score",
            "ai_recommendation",
            "updated_at",
        ])
        
        # Create audit log
        from core.audit import create_audit_log

        create_audit_log(
            actor=request.reviewed_by,
            chama_id=request.chama_id,
            action="ai_membership_review_completed",
            entity_type="MembershipRequest",
            entity_id=request.id,
            metadata={
                "decision": result.get("decision"),
                "confidence": result.get("confidence"),
                "risk_score": result.get("risk_score"),
                "ai_recommended": result.get("ai_recommended", True),
            },
        )
        
        return {
            "status": "completed",
            "request_id": str(membership_request_id),
            "decision": result.get("decision"),
            "risk_score": result.get("risk_score"),
        }
        
    except MembershipRequest.DoesNotExist:
        logger.error(f"Membership request {membership_request_id} not found")
        return {"status": "error", "message": "Request not found"}
    except Exception as e:
        logger.error(f"AI review task failed: {e}")
        return {"status": "error", "message": str(e)}


def get_membership_review_summary(membership_request: MembershipRequest) -> Dict[str, Any]:
    """
    Get a summary of the AI review for display in admin.
    
    Returns a simplified view of the AI decision for non-technical admins.
    """
    if not membership_request.ai_recommendation:
        return {
            "status": "pending",
            "message": "AI review in progress or not available",
        }
    
    try:
        recommendation = json.loads(membership_request.ai_recommendation)
        
        # Simplify for admin display
        risk_band = "LOW"
        if recommendation.get("risk_score", 0) >= 60:
            risk_band = "HIGH"
        elif recommendation.get("risk_score", 0) >= 30:
            risk_band = "MEDIUM"
        
        return {
            "status": "completed",
            "decision": recommendation.get("decision"),
            "risk_band": risk_band,
            "confidence": f"{recommendation.get('confidence', 0) * 100:.0f}%",
            "summary": recommendation.get("reasons", [])[:2],
            "message_to_member": recommendation.get("message_to_member"),
        }
    except (json.JSONDecodeError, AttributeError):
        return {
            "status": "error",
            "message": "Could not parse AI recommendation",
        }


# Model routing helpers
def get_optimal_model_for_task(task_type: str, complexity: str = "normal") -> str:
    """
    Get optimal model based on task type and complexity.
    
    Args:
        task_type: Type of AI task
        complexity: "simple", "normal", or "complex"
    
    Returns:
        Model name to use
    """
    # If simple task, always use fast model
    if task_type in FAST_TASKS or complexity == "simple":
        return "gpt-4o-mini"
    
    # For complex tasks, use accurate model
    if complexity == "complex":
        return "gpt-4o"
    
    # Default routing
    return get_model_for_task(task_type)
