"""
Chatbot API contract tests.

These cover the spec-compatible endpoints under /api/v1/ai/chat/* and /api/v1/ai/chatbot/*.
"""

from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User
from apps.billing.models import FeatureOverride
from apps.chama.models import Chama, MemberStatus, Membership, MembershipRole


class ChatbotAPITestCase(APITestCase):
    """Tests for chatbot API endpoints"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.client = APIClient()
        
        # Create test user
        self.user = User.objects.create_user(phone="+254712345678", full_name="Test User", password="pass12345")
        
        # Create test chama
        self.chama = Chama.objects.create(
            name="Test Chama",
            description="A test savings group",
        )

        FeatureOverride.objects.create(
            chama=self.chama,
            feature_key="ai_basic",
            value=True,
            created_by=self.user,
        )
        
        # Create membership
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
    
    def test_start_conversation_unauthenticated(self):
        """Test that unauthenticated users cannot start conversations"""
        response = self.client.post("/api/v1/ai/chatbot/start/", {})
        self.assertEqual(response.status_code, 401)
    
    def test_start_conversation_authenticated(self):
        """Test starting a conversation as authenticated user"""
        self.client.force_authenticate(user=self.user)
        
        response = self.client.post(
            "/api/v1/ai/chatbot/start/",
            {
                "title": "Test Conversation",
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        self.assertIn("data", payload)
        self.assertIn("conversation", payload["data"])
        self.assertIn("id", payload["data"]["conversation"])
    
    def test_send_message(self):
        """Test sending a message"""
        self.client.force_authenticate(user=self.user)
        
        # First start a conversation
        start_response = self.client.post(
            "/api/v1/ai/chatbot/start/",
            {"title": "Test", "chama_id": str(self.chama.id)},
            format="json",
        )
        conversation_id = start_response.json()["data"]["conversation"]["id"]
        
        # Send a message
        response = self.client.post(
            "/api/v1/ai/chatbot/message/",
            {
                "conversation_id": conversation_id,
                "message": "What is my wallet balance?",
                "stream": False,
            },
            format="json",
        )
        
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        data = payload.get("data") or {}
        self.assertIn("message_id", data)
        self.assertIn("response", data)
    
    def test_get_history(self):
        """Test retrieving conversation history"""
        self.client.force_authenticate(user=self.user)
        
        # Start conversation
        start_response = self.client.post(
            "/api/v1/ai/chatbot/start/",
            {"title": "Test", "chama_id": str(self.chama.id)},
            format="json",
        )
        conversation_id = start_response.json()["data"]["conversation"]["id"]
        
        # Get history
        response = self.client.get(f"/api/v1/ai/chatbot/{conversation_id}/history/")
        
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        data = payload.get("data") or {}
        self.assertIn("messages", data)
        self.assertIn("total", data)
    
    def test_list_conversations(self):
        """Test listing conversations"""
        self.client.force_authenticate(user=self.user)
        
        response = self.client.get("/api/v1/ai/chatbot/conversations/")
        
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        data = payload.get("data") or {}
        self.assertIn("conversations", data)
        self.assertIn("total", data)
    
    def test_suggestions(self):
        """Test getting suggestions"""
        self.client.force_authenticate(user=self.user)
        
        response = self.client.get(f"/api/v1/ai/chatbot/suggestions/?chama_id={self.chama.id}")
        
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        data = payload.get("data") or {}
        self.assertIn("suggestions", data)
        self.assertIsInstance(data["suggestions"], list)
    
    def test_access_other_users_conversation_denied(self):
        """Test that users cannot access other users' conversations"""
        # Create another user
        other_user = User.objects.create_user(phone="+254712345679", full_name="Other User", password="pass12345")
        
        # Authenticate as first user and create conversation
        self.client.force_authenticate(user=self.user)
        start_response = self.client.post(
            "/api/v1/ai/chatbot/start/",
            {"chama_id": str(self.chama.id)},
            format="json",
        )
        conversation_id = start_response.json()["data"]["conversation"]["id"]
        
        # Try to access as other user
        self.client.force_authenticate(user=other_user)
        response = self.client.get(f"/api/v1/ai/chatbot/{conversation_id}/history/")
        
        self.assertEqual(response.status_code, 404)


class ChatbotServiceTestCase(TestCase):
    """Tests for chatbot services"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.user = User.objects.create_user(phone="+254712345678", full_name="Test User", password="pass12345")
        
        self.chama = Chama.objects.create(
            name="Another Test Chama"
        )

        FeatureOverride.objects.create(
            chama=self.chama,
            feature_key="ai_basic",
            value=True,
            created_by=self.user,
        )
        
        self.membership = Membership.objects.create(
            user=self.user,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
    
    def test_context_resolver(self):
        """Test context resolver"""
        from apps.ai.context_resolver import ContextResolver
        
        resolver = ContextResolver(self.user, chama_id=str(self.chama.id))
        context = resolver.resolve()
        
        self.assertIn("user_id", context)
        self.assertIn("role", context)
        self.assertIn("allowed_tools", context)
        self.assertEqual(context["user_id"], str(self.user.id))
    
    def test_orchestration_service_start_conversation(self):
        """Test orchestration service"""
        from apps.ai.chatbot_orchestration import ChatbotOrchestrationService
        
        orchestration = ChatbotOrchestrationService(self.user)
        conversation, suggestions = orchestration.start_conversation(
            title="Test Conversation",
            chama_id=self.chama.id,
        )
        
        self.assertIsNotNone(conversation)
        self.assertEqual(conversation.user, self.user)
        self.assertIsInstance(suggestions, list)
