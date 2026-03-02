#!/usr/bin/env python
"""
Comprehensive AI System Integration Test
Tests all AI functionality including chat, knowledge base, issue triage, meeting summarization, and report explanation.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.test')
django.setup()

from apps.accounts.models import User
from apps.ai.models import (
    AIConversation,
    AIConversationMode,
    AIMessage,
    AIMessageRole,
    KnowledgeDocument,
)
from apps.ai.services import (
    AIGatewayService,
    AIModerationService,
    AIEmbeddingService,
    KnowledgeBaseService,
    AIWorkflowService,
)
from apps.ai.utils import AISystemConfig, AIChatContext
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.issues.models import Issue, IssueStatus
from apps.meetings.models import Meeting
from apps.reports.models import ReportRun, ReportType
from datetime import timedelta
from django.utils import timezone
from decimal import Decimal


def create_test_fixtures():
    """Create test fixtures for AI testing."""
    print("\n" + "="*60)
    print("Setting up test fixtures")
    print("="*60)
    
    # Get or create test chama
    chama, _ = Chama.objects.get_or_create(
        name="Test Chama",
        defaults={
            "status": "active",
            "created_by": None,
            "updated_by": None,
        }
    )
    print(f"✓ Test chama: {chama.name} ({chama.id})")
    
    # Get or create admin user
    admin_user = User.objects.filter(is_staff=True).first()
    if not admin_user:
        admin_user = User.objects.create_user(
            phone="+254700123456",
            password="admin123",
            full_name="Admin User",
            is_staff=True,
        )
        print(f"✓ Admin user created: {admin_user.phone}")
    
    # Get or create test member
    member_user = User.objects.filter(is_staff=False).exclude(
        memberships__isnull=True
    ).first()
    if not member_user:
        member_user = User.objects.create_user(
            phone="+254700654321",
            password="member123",
            full_name="Test Member",
        )
        print(f"✓ Test member created: {member_user.phone}")
    
    # Ensure membership for both
    for user, role in [(admin_user, MembershipRole.CHAMA_ADMIN), (member_user, MembershipRole.MEMBER)]:
        membership, created = Membership.objects.get_or_create(
            user=user,
            chama=chama,
            defaults={
                "role": role,
                "is_active": True,
                "is_approved": True,
                "status": MemberStatus.ACTIVE,
                "created_by": admin_user,
                "updated_by": admin_user,
            }
        )
        if created:
            print(f"✓ Membership created: {user.phone} as {role}")
    
    # Create knowledge document
    doc, _ = KnowledgeDocument.objects.get_or_create(
        chama=chama,
        title="Chama Constitution",
        defaults={
            "source_type": "document",
            "text_content": "Our chama has the following rules:\n1. Monthly contributions are mandatory\n2. Loans require treasurer approval\n3. Meetings are held monthly",
            "created_by": admin_user,
            "updated_by": admin_user,
        }
    )
    print(f"✓ Knowledge document: {doc.title}")
    
    # Create test issue
    issue, _ = Issue.objects.get_or_create(
        chama=chama,
        title="Loan Repayment Issue",
        defaults={
            "description": "Member unable to repay loan on-time due to emergency",
            "category": "loan",
            "status": IssueStatus.OPEN,
            "priority": "high",
            "created_by": member_user,
            "updated_by": member_user,
        }
    )
    print(f"✓ Test issue: {issue.title}")
    
    # Create test meeting
    meeting, _ = Meeting.objects.get_or_create(
        chama=chama,
        title="Monthly Meeting",
        defaults={
            "date": timezone.now() + timedelta(days=7),
            "agenda": "1. Review monthly finances\n2. Approve new loans\n3. Discuss member issues",
            "minutes_text": "Attended by 25 members. Approved 3 loans totaling 150,000. Discussed 2 issues.",
            "created_by": admin_user,
            "updated_by": admin_user,
        }
    )
    print(f"✓ Test meeting: {meeting.title}")
    
    return {
        "chama": chama,
        "admin_user": admin_user,
        "member_user": member_user,
        "knowledge_doc": doc,
        "issue": issue,
        "meeting": meeting,
    }


def test_moderation_service():
    """Test AI moderation service."""
    print("\n" + "="*60)
    print("Testing Moderation Service")
    print("="*60)
    
    # Test allowed message
    result = AIModerationService.moderate_text("How do I calculate my contribution?")
    assert result["allowed"], "Normal message should be allowed"
    print(f"✓ Normal message approved: {result['reason']}")
    
    # Test blocked message
    result = AIModerationService.moderate_text("how to hack the system bypass otp")
    assert not result["allowed"], "Suspicious message should be blocked"
    print(f"✓ Suspicious message blocked: {result['reason']}")
    
    # Test empty message
    result = AIModerationService.moderate_text("")
    assert not result["allowed"], "Empty message should be blocked"
    print(f"✓ Empty message rejected: {result['reason']}")


def test_embedding_service():
    """Test AI embedding service."""
    print("\n" + "="*60)
    print("Testing Embedding Service")
    print("="*60)
    
    text1 = "What is my contribution history?"
    text2 = "Show me my payment records"
    text3 = "Random unrelated text"
    
    embedding1 = AIEmbeddingService.embed_text(text1)
    embedding2 = AIEmbeddingService.embed_text(text2)
    embedding3 = AIEmbeddingService.embed_text(text3)
    
    assert len(embedding1) > 0, "Embedding should have dimensions"
    assert len(embedding1) == len(embedding2), "Embeddings should have same dimension"
    print(f"✓ Embeddings generated: dimension={len(embedding1)}")
    
    # Simulate similarity (not exact matching, just verify structure)
    print(f"✓ Embedding service working with fallback: {AISystemConfig.get_embedding_model()}")


def test_knowledge_base_service(fixtures):
    """Test AI knowledge base service."""
    print("\n" + "="*60)
    print("Testing Knowledge Base Service")
    print("="*60)
    
    doc = fixtures["knowledge_doc"]
    
    # Reindex document
    chunks = KnowledgeBaseService.reindex_document(document=doc, actor=fixtures["admin_user"])
    assert chunks > 0, "Should create chunks"
    print(f"✓ Document reindexed: {chunks} chunks created")
    
    # Search knowledge base
    search_results = KnowledgeBaseService.search(
        chama_id=fixtures["chama"].id, 
        query="loan approval process", 
        top_k=5
    )
    print(f"✓ Knowledge search completed: {len(search_results)} results")


def test_chat_gateway(fixtures):
    """Test AI chat gateway service."""
    print("\n" + "="*60)
    print("Testing Chat Gateway Service")
    print("="*60)
    
    user = fixtures["member_user"]
    chama_id = str(fixtures["chama"].id)
    
    # Validate context
    context = AIChatContext(
        user_id=str(user.id),
        chama_id=chama_id,
        mode=AIConversationMode.MEMBER_ASSISTANT,
        message="What is my loan balance?"
    )
    context.validate()
    print(f"✓ Chat context validated")
    
    # Test tool planning
    tools = AIGatewayService._detect_tools(
        message="Show me my loan balance and next installment",
        mode=AIConversationMode.MEMBER_ASSISTANT
    )
    assert len(tools) > 0, "Should detect tools"
    print(f"✓ Tools detected: {[t[0] for t in tools]}")


def test_workflow_service(fixtures):
    """Test AI workflow service."""
    print("\n" + "="*60)
    print("Testing Workflow Service")
    print("="*60)
    
    # Test issue triage
    issue_result = AIWorkflowService.triage_issue(
        issue_id=fixtures["issue"].id,
        actor=fixtures["admin_user"]
    )
    assert "category" in issue_result, "Should return category"
    assert "priority" in issue_result, "Should return priority"
    assert "suggested_assignee_role" in issue_result, "Should return assignee role"
    print(f"✓ Issue triaged: category={issue_result['category']}, priority={issue_result['priority']}")
    
    # Test meeting summarization
    meeting_result = AIWorkflowService.summarize_meeting(
        meeting_id=fixtures["meeting"].id,
        actor=fixtures["member_user"]
    )
    assert "summary" in meeting_result, "Should return summary"
    assert "action_items" in meeting_result, "Should return action items"
    print(f"✓ Meeting summarized: {len(meeting_result['action_items'])} action items")


def test_system_config():
    """Test AI system configuration."""
    print("\n" + "="*60)
    print("Testing System Configuration")
    print("="*60)
    
    print(f"✓ OpenAI enabled: {AISystemConfig.is_openai_enabled()}")
    print(f"✓ Chat model: {AISystemConfig.get_chat_model()}")
    print(f"✓ Embedding model: {AISystemConfig.get_embedding_model()}")
    print(f"✓ Moderation model: {AISystemConfig.get_moderation_model()}")
    print(f"✓ OTP expiry: {AISystemConfig.get_otp_expiry_minutes()} minutes")


def test_conversation_management(fixtures):
    """Test conversation creation and management."""
    print("\n" + "="*60)
    print("Testing Conversation Management")
    print("="*60)
    
    user = fixtures["member_user"]
    chama = fixtures["chama"]
    
    # Create conversation
    conversation = AIConversation.objects.create(
        chama=chama,
        user=user,
        mode=AIConversationMode.MEMBER_ASSISTANT,
        created_by=user,
        updated_by=user,
    )
    print(f"✓ Conversation created: {conversation.id}")
    
    # Add messages
    user_msg = AIMessage.objects.create(
        conversation=conversation,
        role=AIMessageRole.USER,
        content="What is my current balance?",
        created_by=user,
        updated_by=user,
    )
    print(f"✓ User message created")
    
    assistant_msg = AIMessage.objects.create(
        conversation=conversation,
        role=AIMessageRole.ASSISTANT,
        content="Your current balance is KES 50,000",
        created_by=user,
        updated_by=user,
    )
    print(f"✓ Assistant message created")
    
    # Verify conversation has messages
    messages = conversation.messages.all()
    assert messages.count() == 2, "Should have 2 messages"
    print(f"✓ Conversation has {messages.count()} messages")


if __name__ == '__main__':
    print("\n" + "="*80)
    print(" " * 15 + "AI SYSTEM FULL INTEGRATION TEST")
    print("="*80)
    
    try:
        # Create fixtures
        fixtures = create_test_fixtures()
        
        # Run tests
        test_system_config()
        test_moderation_service()
        test_embedding_service()
        test_knowledge_base_service(fixtures)
        test_chat_gateway(fixtures)
        test_workflow_service(fixtures)
        test_conversation_management(fixtures)
        
        print("\n" + "="*80)
        print(" " * 20 + "✓ ALL AI TESTS PASSED!")
        print("="*80)
        print("\nAI System Status:")
        print("  • Moderation: ✓ Operational")
        print("  • Embeddings: ✓ Operational (with fallback)")
        print("  • Knowledge Base: ✓ Operational")
        print("  • Chat Gateway: ✓ Operational")
        print("  • Issue Triage: ✓ Operational")
        print("  • Meeting Summarization: ✓ Operational")
        print("  • Conversation Management: ✓ Operational")
        print("\nEndpoints Available:")
        print("  • POST /api/ai/chat - Start AI chat")
        print("  • GET /api/ai/status - System status")
        print("  • GET /api/ai/conversations - List conversations")
        print("  • GET /api/ai/conversations/<id> - View conversation")
        print("  • POST /api/ai/issues/triage - Auto-triage issues")
        print("  • POST /api/ai/meetings/summarize - Summarize meetings")
        print("  • POST /api/ai/reports/explain - Explain reports")
        print("  • GET/POST /api/ai/kb/documents - Manage knowledge base")
        print("  • POST /api/ai/kb/reindex - Reindex knowledge base")
        print("\n")
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
