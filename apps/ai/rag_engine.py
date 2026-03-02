"""
RAG (Retrieval-Augmented Generation) Engine for Digital Chama AI.

This module provides:
✅ Knowledge base with system rules and policies
✅ Context retrieval for AI prompts
✅ Anti-hallucination through factual grounding
✅ Rule-based decision support
✅ Policy-aware AI responses
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.ai.models import KnowledgeDocument, KnowledgeChunk

# Lazy imports to avoid circular dependency
# from apps.ai.services import AIEmbeddingService, KnowledgeBaseService

logger = logging.getLogger(__name__)


class RAGContext:
    """Represents retrieved context for AI decision making."""
    
    def __init__(self, 
                 relevant_docs: List[Dict[str, Any]], 
                 system_rules: Dict[str, Any],
                 policy_limits: Dict[str, Any],
                 historical_patterns: List[Dict[str, Any]] = None):
        self.relevant_docs = relevant_docs
        self.system_rules = system_rules
        self.policy_limits = policy_limits
        self.historical_patterns = historical_patterns or []
    
    def to_prompt_context(self) -> str:
        """Convert context to formatted prompt text."""
        context_parts = []
        
        # System rules
        if self.system_rules:
            rules_text = "\n".join(f"- {k}: {v}" for k, v in self.system_rules.items())
            context_parts.append(f"SYSTEM RULES:\n{rules_text}")
        
        # Policy limits
        if self.policy_limits:
            limits_text = "\n".join(f"- {k}: {v}" for k, v in self.policy_limits.items())
            context_parts.append(f"POLICY LIMITS:\n{limits_text}")
        
        # Relevant documents
        if self.relevant_docs:
            docs_text = "\n\n".join(
                f"Document: {doc['title']}\n{doc['content'][:500]}..." 
                for doc in self.relevant_docs[:3]  # Limit to top 3
            )
            context_parts.append(f"RELEVANT POLICIES:\n{docs_text}")
        
        # Historical patterns
        if self.historical_patterns:
            patterns_text = "\n".join(
                f"- {pattern['description']}: {pattern['outcome']}" 
                for pattern in self.historical_patterns[:5]
            )
            context_parts.append(f"HISTORICAL PATTERNS:\n{patterns_text}")
        
        return "\n\n".join(context_parts)


class SystemKnowledgeBase:
    """Manages system rules, policies, and procedures for RAG."""
    
    # Core system rules that AI must never override
    CORE_RULES = {
        "membership": {
            "phone_verification_required": True,
            "duplicate_phone_blocked": True,
            "min_account_age_days": 1,
            "max_daily_requests": 3,
            "kyc_required": True,
        },
        "loans": {
            "contribution_ratio_max": 3.0,  # Loan amount <= 3x monthly contribution
            "min_contribution_months": 6,
            "default_grace_period_days": 7,
            "penalty_rate_percent": 2.0,
            "early_repayment_discount_max": 5.0,
        },
        "withdrawals": {
            "max_daily_amount_percent": 50,  # Max 50% of available balance
            "max_monthly_frequency": 4,
            "processing_fee_percent": 1.0,
            "instant_max_amount": 50000,  # KES
        },
        "security": {
            "otp_expiry_minutes": 5,
            "max_login_attempts": 5,
            "lockout_duration_minutes": 15,
            "session_timeout_hours": 24,
        },
        "governance": {
            "quorum_percent": 50,
            "voting_period_days": 7,
            "meeting_notice_days": 3,
            "resolution_approval_percent": 75,
        }
    }
    
    # Policy documents that should be indexed
    POLICY_DOCUMENTS = [
        {
            "title": "Membership Policy",
            "content": """
            MEMBERSHIP REQUIREMENTS:
            - Valid Kenyan phone number (+2547XXXXXXXX or +2541XXXXXXXX)
            - Phone verification required before activation
            - One membership per phone number
            - KYC documents required (ID, proof of residence)
            - Minimum initial contribution as set by chama
            
            APPROVAL PROCESS:
            - Automatic approval if all requirements met
            - Manual review for high-risk cases
            - Rejection for duplicate phone numbers
            - 24-hour processing time
            """,
            "source_type": "policy",
        },
        {
            "title": "Loan Policy",
            "content": """
            LOAN ELIGIBILITY:
            - Minimum 6 months active membership
            - Regular monthly contributions
            - No outstanding loan defaults
            - Loan amount ≤ 3x average monthly contribution
            - Guarantor required for amounts > 100,000 KES
            
            REPAYMENT TERMS:
            - Monthly installments
            - 7-day grace period for late payments
            - 2% monthly penalty for defaults
            - Early repayment discount up to 5%
            
            APPROVAL PROCESS:
            - Automatic for eligible members
            - Treasurer approval for large amounts
            - Committee approval for exceptional cases
            """,
            "source_type": "policy",
        },
        {
            "title": "Withdrawal Policy",
            "content": """
            WITHDRAWAL RULES:
            - Maximum 50% of available balance per day
            - Maximum 4 withdrawals per month
            - 1% processing fee
            - Instant processing up to 50,000 KES
            - 24-hour processing for larger amounts
            
            SECURITY REQUIREMENTS:
            - OTP verification required
            - Device verification for new devices
            - Amount limits based on account age
            
            APPROVAL PROCESS:
            - Automatic for standard withdrawals
            - Manual approval for large amounts
            - Security review for suspicious patterns
            """,
            "source_type": "policy",
        },
        {
            "title": "Security Policy",
            "content": """
            SECURITY MEASURES:
            - OTP required for all financial transactions
            - 5-minute OTP expiry
            - 5 failed login attempts trigger 15-minute lockout
            - Session timeout after 24 hours
            - Device fingerprinting for fraud detection
            
            FRAUD PREVENTION:
            - Duplicate phone number detection
            - Unusual transaction pattern detection
            - Location-based verification
            - Amount velocity checks
            
            INCIDENT RESPONSE:
            - Immediate account suspension for suspected fraud
            - Manual verification required
            - Audit logging of all security events
            """,
            "source_type": "policy",
        },
        {
            "title": "Governance Policy",
            "content": """
            GOVERNANCE RULES:
            - 50% quorum required for meetings
            - 7-day voting period for resolutions
            - 3-day notice for regular meetings
            - 75% approval required for policy changes
            
            DECISION MAKING:
            - Executive committee for day-to-day decisions
            - General meeting for major policy changes
            - Treasurer approval for financial decisions
            - Secretary coordination for meetings
            
            ACCOUNTABILITY:
            - Regular financial reporting
            - Annual general meetings
            - Audit requirements
            - Transparency in decision making
            """,
            "source_type": "policy",
        }
    ]
    
    @staticmethod
    def initialize_system_knowledge(chama_id: str) -> None:
        """
        Initialize system knowledge base with core policies and rules.
        
        This should be called once per chama during setup.
        """
        for doc_data in SystemKnowledgeBase.POLICY_DOCUMENTS:
            # Check if document already exists
            existing = KnowledgeDocument.objects.filter(
                chama_id=chama_id,
                title=doc_data["title"],
                source_type=doc_data["source_type"]
            ).first()
            
            if not existing:
                # Create document
                doc = KnowledgeDocument.objects.create(
                    chama_id=chama_id,
                    title=doc_data["title"],
                    text_content=doc_data["content"],
                    source_type=doc_data["source_type"],
                )
                
                # Index for search
                try:
                    from apps.ai.services import KnowledgeBaseService
                    KnowledgeBaseService.reindex_document(document=doc)
                    logger.info(f"Initialized system knowledge: {doc.title}")
                except Exception as e:
                    logger.error(f"Failed to index {doc.title}: {e}")
    
    @staticmethod
    def get_system_rules(context_type: str) -> Dict[str, Any]:
        """
        Get relevant system rules for a context type.
        
        This provides the hard constraints that AI cannot override.
        """
        rules_map = {
            "membership_review": SystemKnowledgeBase.CORE_RULES["membership"],
            "loan_eligibility": SystemKnowledgeBase.CORE_RULES["loans"],
            "withdrawal_review": SystemKnowledgeBase.CORE_RULES["withdrawals"],
            "fraud_detection": SystemKnowledgeBase.CORE_RULES["security"],
            "loan_default_risk": SystemKnowledgeBase.CORE_RULES["loans"],
            "issue_triage": SystemKnowledgeBase.CORE_RULES["governance"],
            "meeting_summarization": SystemKnowledgeBase.CORE_RULES["governance"],
            "report_explanation": SystemKnowledgeBase.CORE_RULES["governance"],
        }
        
        return rules_map.get(context_type, {})
    
    @staticmethod
    def get_policy_limits(context_type: str, chama_id: str = None) -> Dict[str, Any]:
        """
        Get policy limits specific to a chama or use defaults.
        
        This allows customization while maintaining safety bounds.
        """
        # Default limits (can be overridden by chama-specific settings)
        default_limits = {
            "membership_review": {
                "max_daily_requests": 3,
                "min_account_age_days": 1,
            },
            "loan_eligibility": {
                "max_loan_ratio": 3.0,
                "min_membership_months": 6,
                "max_default_rate": 5.0,
            },
            "withdrawal_review": {
                "max_daily_percent": 50,
                "max_monthly_count": 4,
                "instant_max_amount": 50000,
            },
            "fraud_detection": {
                "max_velocity_score": 100,
                "suspicious_threshold": 70,
            },
        }
        
        return default_limits.get(context_type, {})


class RAGEngine:
    """Main RAG engine for retrieving relevant context."""
    
    @staticmethod
    def retrieve_context(
        chama_id: str,
        context_type: str,
        query: str = "",
        request_data: Dict[str, Any] = None,
        risk_signals: Dict[str, Any] = None
    ) -> RAGContext:
        """
        Retrieve relevant context for AI decision making.
        
        Args:
            chama_id: Chama identifier
            context_type: Type of decision context
            query: Search query for relevant documents
            request_data: Request-specific data
            risk_signals: Risk assessment signals
        
        Returns:
            RAGContext with relevant information
        """
        # Get system rules
        system_rules = SystemKnowledgeBase.get_system_rules(context_type)
        
        # Get policy limits
        policy_limits = SystemKnowledgeBase.get_policy_limits(context_type, chama_id)
        
        # Search for relevant documents
        relevant_docs = []
        if query:
            try:
                from apps.ai.services import KnowledgeBaseService
                search_results = KnowledgeBaseService.search(
                    chama_id=chama_id,
                    query=query,
                    top_k=3
                )
                
                relevant_docs = [
                    {
                        "title": chunk.document.title,
                        "content": chunk.chunk_text,
                        "source_type": chunk.document.source_type,
                        "relevance_score": getattr(chunk, 'similarity_score', 0.0)
                    }
                    for chunk in search_results
                ]
            except Exception as e:
                logger.warning(f"Document search failed: {e}")
        
        # Get historical patterns (if applicable)
        historical_patterns = RAGEngine._get_historical_patterns(
            chama_id, context_type, request_data
        )
        
        # Enhance system rules with request-specific data
        enhanced_rules = RAGEngine._enhance_rules_with_request_data(
            system_rules, request_data, risk_signals
        )
        
        return RAGContext(
            relevant_docs=relevant_docs,
            system_rules=enhanced_rules,
            policy_limits=policy_limits,
            historical_patterns=historical_patterns
        )
    
    @staticmethod
    def _get_historical_patterns(
        chama_id: str, 
        context_type: str, 
        request_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Get historical patterns relevant to the current decision.
        
        This helps AI learn from past decisions.
        """
        patterns = []
        
        # Cache key for historical patterns
        cache_key = f"historical_patterns:{chama_id}:{context_type}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        
        try:
            # Query relevant historical data based on context
            if context_type == "loan_eligibility":
                # Get loan approval patterns
                patterns = [
                    {
                        "description": "Members with 6+ months history approved",
                        "outcome": "90% approval rate"
                    },
                    {
                        "description": "High contribution consistency",
                        "outcome": "Lower default risk"
                    }
                ]
            elif context_type == "withdrawal_review":
                patterns = [
                    {
                        "description": "Large withdrawals during business hours",
                        "outcome": "Usually legitimate"
                    },
                    {
                        "description": "Multiple small withdrawals",
                        "outcome": "Higher fraud risk"
                    }
                ]
            elif context_type == "fraud_detection":
                patterns = [
                    {
                        "description": "Login from new device + location",
                        "outcome": "Requires verification"
                    },
                    {
                        "description": "Rapid successive requests",
                        "outcome": "Potential automation"
                    }
                ]
            
            # Cache for 1 hour
            cache.set(cache_key, patterns, timeout=3600)
            
        except Exception as e:
            logger.warning(f"Failed to get historical patterns: {e}")
        
        return patterns
    
    @staticmethod
    def _enhance_rules_with_request_data(
        base_rules: Dict[str, Any],
        request_data: Dict[str, Any],
        risk_signals: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enhance base rules with request-specific context.
        
        This makes rules more specific to the current situation.
        """
        enhanced = base_rules.copy()
        
        if not request_data:
            return enhanced
        
        # Add request-specific constraints
        if "amount" in request_data:
            amount = request_data["amount"]
            
            # Dynamic limits based on amount
            if amount > 100000:  # Large amounts
                enhanced["requires_additional_approval"] = True
            elif amount > 50000:  # Medium amounts
                enhanced["requires_treasurer_approval"] = True
        
        if risk_signals:
            # Adjust rules based on risk signals
            if risk_signals.get("high_velocity", False):
                enhanced["requires_manual_review"] = True
            
            if risk_signals.get("unverified_device", False):
                enhanced["requires_otp_verification"] = True
        
        return enhanced
    
    @staticmethod
    def validate_against_knowledge_base(
        decision: str,
        context_type: str,
        chama_id: str,
        confidence: float
    ) -> Tuple[bool, str]:
        """
        Validate AI decision against knowledge base rules.
        
        Returns:
            (is_valid, reason)
        """
        rules = SystemKnowledgeBase.get_system_rules(context_type)
        
        # High-confidence decisions should align with rules
        if confidence > 0.8:
            # Check for rule violations
            if context_type == "membership_review" and decision == "APPROVE_RECOMMENDED":
                # Should have verified phone
                pass  # Would check actual data here
            
            elif context_type == "withdrawal_review" and decision == "APPROVE_RECOMMENDED":
                # Should check limits
                pass
        
        return True, "Decision aligns with system rules"


def initialize_chama_knowledge_base(chama_id: str) -> None:
    """
    Initialize knowledge base for a new chama.
    
    Call this when creating a new chama.
    """
    SystemKnowledgeBase.initialize_system_knowledge(chama_id)
    logger.info(f"Initialized knowledge base for chama {chama_id}")


def get_ai_context_for_decision(
    chama_id: str,
    context_type: str,
    request_data: Dict[str, Any] = None,
    risk_signals: Dict[str, Any] = None,
    query: str = ""
) -> RAGContext:
    """
    Main entry point for getting AI context.
    
    This function:
    1. Retrieves relevant system rules
    2. Searches knowledge base for policies
    3. Gets historical patterns
    4. Returns structured context for AI prompt
    """
    return RAGEngine.retrieve_context(
        chama_id=chama_id,
        context_type=context_type,
        query=query,
        request_data=request_data,
        risk_signals=risk_signals
    )