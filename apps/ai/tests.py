from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.ai import views
from apps.ai.chatbot_views import ChatbotSendMessageView, ChatbotStartConversationView, ChatbotClearConversationView
from apps.ai.models import (
    AIAnswerFeedback,
    AIConversation,
    AIConversationMode,
    AIMessage,
    AIMessageRole,
)
from apps.billing.models import FeatureOverride
from apps.billing.services import clear_entitlements_cache
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus


class AIViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        user_model = get_user_model()

        self.member = user_model.objects.create_user(
            phone="+254700000001",
            full_name="Member One",
            password="password123",
        )
        self.admin = user_model.objects.create_user(
            phone="+254700000002",
            full_name="Admin One",
            password="password123",
        )
        self.other_member = user_model.objects.create_user(
            phone="+254700000003",
            full_name="Member Two",
            password="password123",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254700000004",
            full_name="Treasurer One",
            password="password123",
        )

        self.chama = Chama.objects.create(name="AI Regression Chama")

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.other_member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.treasurer,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def test_ai_feedback_does_not_allow_cross_user_message_ids(self):
        conversation = AIConversation.objects.create(
            user=self.other_member,
            chama=self.chama,
            mode=AIConversationMode.MEMBER_ASSISTANT,
        )
        message = AIMessage.objects.create(
            conversation=conversation,
            role=AIMessageRole.ASSISTANT,
            content="Here is a private AI answer.",
        )

        request = self.factory.post(
            "/api/v1/ai/feedback/",
            {
                "message_id": str(message.id),
                "rating": "thumbs_up",
                "comment": "Looks good",
            },
            format="json",
        )
        force_authenticate(request, user=self.member)

        response = views.ai_feedback(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(AIAnswerFeedback.objects.count(), 0)

    def test_ai_conversations_returns_computed_title(self):
        conversation = AIConversation.objects.create(
            user=self.member,
            chama=self.chama,
            mode=AIConversationMode.MEMBER_ASSISTANT,
        )
        AIMessage.objects.create(
            conversation=conversation,
            role=AIMessageRole.USER,
            content="  How much   have I contributed this month?  ",
        )

        request = self.factory.get(
            f"/api/v1/ai/conversations/?chama_id={self.chama.id}"
        )
        force_authenticate(request, user=self.member)

        response = views.ai_conversations(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["conversations"]), 1)
        self.assertTrue(
            response.data["conversations"][0]["title"].startswith(
                "How much have I contributed this month?"
            )
        )

    def test_insights_refresh_uses_role_check_without_missing_method(self):
        FeatureOverride.objects.create(
            chama=self.chama,
            feature_key="ai_advanced",
            value=True,
            created_by=self.admin,
        )

        request = self.factory.post(
            f"/api/v1/ai/insights/{self.chama.id}/refresh/",
            {},
            format="json",
        )
        force_authenticate(request, user=self.admin)

        with patch("apps.ai.views.InsightsEngine.generate_all_insights") as generate_mock:
            response = views.insights_refresh(request, chama_id=self.chama.id)

        self.assertEqual(response.status_code, 200)
        generate_mock.assert_called_once_with(self.chama.id)
        self.assertEqual(response.data["insights"], [])

    def test_member_cannot_access_chama_wallet_totals_from_embedded_stream(self):
        request = self.factory.post(
            "/api/v1/ai/chat/stream/",
            {
                "message": "Show chama wallet totals",
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_chat_stream(request)

        self.assertEqual(response.status_code, 403)
        self.assertIn("outside your role permissions", str(response.data["detail"]))

    def test_treasurer_can_access_unpaid_members_tool(self):
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_unpaid_members",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.treasurer)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["tool"], "get_unpaid_members")

    def test_ai_plan_gating_blocks_stream_when_ai_disabled(self):
        FeatureOverride.objects.create(
            chama=self.chama,
            feature_key="ai_basic",
            value=False,
            created_by=self.admin,
        )
        FeatureOverride.objects.create(
            chama=self.chama,
            feature_key="ai_advanced",
            value=False,
            created_by=self.admin,
        )
        clear_entitlements_cache(self.chama)

        request = self.factory.post(
            "/api/v1/ai/chat/stream/",
            {
                "message": "Show my wallet summary",
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_chat_stream(request)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data["upgrade_required"])

    def test_chatbot_start_and_message_endpoints_work(self):
        FeatureOverride.objects.create(
            chama=self.chama,
            feature_key="ai_basic",
            value=True,
            created_by=self.admin,
        )

        start_request = self.factory.post(
            "/api/v1/ai/chat/start/",
            {"chama_id": str(self.chama.id), "title": "Help"},
            format="json",
        )
        force_authenticate(start_request, user=self.member)
        start_response = ChatbotStartConversationView.as_view()(start_request)
        self.assertEqual(start_response.status_code, 201)
        self.assertTrue(start_response.data["success"])
        conversation_id = start_response.data["data"]["conversation"]["id"]

        msg_request = self.factory.post(
            "/api/v1/ai/chat/message/",
            {"conversation_id": conversation_id, "message": "What's my wallet balance?"},
            format="json",
        )
        force_authenticate(msg_request, user=self.member)
        msg_response = ChatbotSendMessageView.as_view()(msg_request)
        self.assertEqual(msg_response.status_code, 200)
        self.assertTrue(msg_response.data["success"])
        self.assertIn("response", msg_response.data["data"])

        clear_request = self.factory.post(
            f"/api/v1/ai/chat/{conversation_id}/clear/",
            {},
            format="json",
        )
        force_authenticate(clear_request, user=self.member)
        clear_response = ChatbotClearConversationView.as_view()(clear_request, conversation_id=conversation_id)
        self.assertEqual(clear_response.status_code, 200)
        self.assertTrue(clear_response.data["success"])


class AIToolsViewTests(TestCase):
    """Tests for AI tool execution endpoints."""

    def setUp(self):
        self.factory = APIRequestFactory()
        user_model = get_user_model()

        self.member = user_model.objects.create_user(
            phone="+254700000011",
            full_name="Test Member",
            password="password123",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254700000012",
            full_name="Test Treasurer",
            password="password123",
        )
        self.admin = user_model.objects.create_user(
            phone="+254700000013",
            full_name="Test Admin",
            password="password123",
        )

        self.chama = Chama.objects.create(name="Tools Test Chama")

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.treasurer,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.admin,
            chama=self.chama,
            role=MembershipRole.CHAMA_ADMIN,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def test_get_my_wallet_summary_tool(self):
        """Test member can get their own wallet summary."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_my_wallet_summary",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertIn("result", response.data)
        result = response.data["result"]
        self.assertIn("total_contributions", result)
        self.assertIn("total_withdrawals", result)
        self.assertIn("net_balance", result)

    def test_get_chama_wallet_summary_requires_treasurer(self):
        """Test that only treasurer/admin can get chama wallet summary."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_chama_wallet_summary",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        result = response.data["result"]
        self.assertFalse(result.get("available", True))

    def test_treasurer_can_get_chama_wallet_summary(self):
        """Test treasurer can get chama wallet summary."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_chama_wallet_summary",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.treasurer)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        result = response.data["result"]
        self.assertTrue(result.get("available", False))

    def test_generate_statement_pdf_tool(self):
        """Test generate_statement_pdf tool returns statement data."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "generate_statement_pdf",
                "params": {"period_months": 6},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        result = response.data["result"]
        self.assertTrue(result.get("available", False))
        self.assertIn("summary", result)
        self.assertIn("contributions", result)
        self.assertIn("statement_id", result)

    def test_unknown_tool_returns_error(self):
        """Test that unknown tool returns appropriate error."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "nonexistent_tool",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 501)


class AIContextAndSuggestionsTests(TestCase):
    """Tests for AI context and suggestions endpoints."""

    def setUp(self):
        self.factory = APIRequestFactory()
        user_model = get_user_model()

        self.member = user_model.objects.create_user(
            phone="+254700000021",
            full_name="Context Test Member",
            password="password123",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254700000022",
            full_name="Context Test Treasurer",
            password="password123",
        )

        self.chama = Chama.objects.create(name="Context Test Chama")

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.treasurer,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def test_ai_context_returns_member_info(self):
        """Test AI context endpoint returns correct user info."""
        request = self.factory.get(
            f"/api/v1/ai/context/?chama_id={self.chama.id}"
        )
        force_authenticate(request, user=self.member)

        response = views.ai_context(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("user", response.data)
        self.assertIn("chama", response.data)
        self.assertIn("role", response.data)
        self.assertEqual(response.data["role"], "MEMBER")

    def test_ai_context_returns_treasurer_info(self):
        """Test AI context endpoint returns correct treasurer info."""
        request = self.factory.get(
            f"/api/v1/ai/context/?chama_id={self.chama.id}"
        )
        force_authenticate(request, user=self.treasurer)

        response = views.ai_context(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "TREASURER")

    def test_ai_suggestions_returns_member_suggestions(self):
        """Test AI suggestions endpoint returns role-appropriate suggestions."""
        request = self.factory.get(
            f"/api/v1/ai/suggestions/?chama_id={self.chama.id}"
        )
        force_authenticate(request, user=self.member)

        response = views.ai_suggestions(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("suggestions", response.data)
        suggestions = response.data["suggestions"]
        self.assertIsInstance(suggestions, list)

    def test_ai_suggestions_returns_treasurer_suggestions(self):
        """Test AI suggestions endpoint returns treasurer-specific suggestions."""
        request = self.factory.get(
            f"/api/v1/ai/suggestions/?chama_id={self.chama.id}"
        )
        force_authenticate(request, user=self.treasurer)

        response = views.ai_suggestions(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("suggestions", response.data)


class AIStreamingAndStoppingTests(TestCase):
    """Tests for AI chat streaming and stopping endpoints."""

    def setUp(self):
        self.factory = APIRequestFactory()
        user_model = get_user_model()

        self.member = user_model.objects.create_user(
            phone="+254700000031",
            full_name="Streaming Test Member",
            password="password123",
        )

        self.chama = Chama.objects.create(name="Streaming Test Chama")

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def test_chat_stream_requires_authentication(self):
        """Test chat stream endpoint requires authentication."""
        request = self.factory.post(
            "/api/v1/ai/chat/stream/",
            {
                "message": "Hello",
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)

        response = views.ai_chat_stream(request)

        # Should return 401 or 403 for unauthenticated request
        self.assertIn(response.status_code, [401, 403])

    def test_chat_stream_with_valid_member(self):
        """Test chat stream works with authenticated member."""
        request = self.factory.post(
            "/api/v1/ai/chat/stream/",
            {
                "message": "What is my contribution status?",
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_chat_stream(request)

        # Should return streaming response (200) or rate limit
        self.assertIn(response.status_code, [200, 429])

    def test_chat_stream_emits_checking_stage_immediately(self):
        """Test the stream yields a quick stage-a checking message before final answer chunks."""
        request = self.factory.post(
            "/api/v1/ai/chat/stream/",
            {
                "message": "What is my contribution status?",
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_chat_stream(request)

        self.assertEqual(response.status_code, 200)
        stream = iter(response.streaming_content)
        first_chunk = next(stream)
        second_chunk = next(stream)
        if isinstance(first_chunk, bytes):
            first_chunk = first_chunk.decode()
        if isinstance(second_chunk, bytes):
            second_chunk = second_chunk.decode()
        self.assertIn("stage", first_chunk)
        self.assertIn("checking your records", second_chunk)

    def test_chat_stop_endpoint(self):
        """Test chat stop endpoint works."""
        request = self.factory.post(
            "/api/v1/ai/chat/stop/",
            {
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_chat_stop(request)

        # Should return 200 with stop confirmation
        self.assertEqual(response.status_code, 200)


class AIToolsCachingTests(TestCase):
    """Tests for AI tools caching functionality."""

    def setUp(self):
        self.factory = APIRequestFactory()
        user_model = get_user_model()

        self.member = user_model.objects.create_user(
            phone="+254700000041",
            full_name="Caching Test Member",
            password="password123",
        )
        self.treasurer = user_model.objects.create_user(
            phone="+254700000042",
            full_name="Caching Test Treasurer",
            password="password123",
        )

        self.chama = Chama.objects.create(name="Caching Test Chama")

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.treasurer,
            chama=self.chama,
            role=MembershipRole.TREASURER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def test_cache_key_generation(self):
        """Test cache key generation is consistent."""
        from apps.ai.ai_tools import generate_cache_key

        key1 = generate_cache_key("test", "arg1", "arg2")
        key2 = generate_cache_key("test", "arg1", "arg2")

        self.assertEqual(key1, key2)

    def test_cache_key_with_kwargs(self):
        """Test cache key generation with kwargs."""
        from apps.ai.ai_tools import generate_cache_key

        key1 = generate_cache_key("test", period=6, member_id="123")
        key2 = generate_cache_key("test", period=6, member_id="123")

        self.assertEqual(key1, key2)

    def test_tool_execution_returns_cached_result(self):
        """Test that repeated tool calls return cached results."""
        from apps.ai.ai_tools import ToolRouter

        # First call - should execute
        result1 = ToolRouter.get_my_wallet_summary(self.member, self.chama)

        # Second call - should use cache
        result2 = ToolRouter.get_my_wallet_summary(self.member, self.chama)

        # Results should be identical
        self.assertEqual(result1, result2)

    def test_treasurer_tool_caching(self):
        """Test treasurer-specific tools are cached."""
        from apps.ai.ai_tools import ToolRouter

        # First call
        result1 = ToolRouter.get_chama_wallet_summary(self.chama, self.treasurer)

        # Second call - should use cache
        result2 = ToolRouter.get_chama_wallet_summary(self.chama, self.treasurer)

        # Results should be identical
        self.assertEqual(result1, result2)


class AIPermissionsTests(TestCase):
    """Tests for AI role-based permissions."""

    def setUp(self):
        self.factory = APIRequestFactory()
        user_model = get_user_model()

        self.member = user_model.objects.create_user(
            phone="+254700000051",
            full_name="Perm Test Member",
            password="password123",
        )
        self.secretary = user_model.objects.create_user(
            phone="+254700000052",
            full_name="Perm Test Secretary",
            password="password123",
        )
        self.auditor = user_model.objects.create_user(
            phone="+254700000053",
            full_name="Perm Test Auditor",
            password="password123",
        )

        self.chama = Chama.objects.create(name="Perms Test Chama")

        Membership.objects.create(
            user=self.member,
            chama=self.chama,
            role=MembershipRole.MEMBER,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.secretary,
            chama=self.chama,
            role=MembershipRole.SECRETARY,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )
        Membership.objects.create(
            user=self.auditor,
            chama=self.chama,
            role=MembershipRole.AUDITOR,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
        )

    def test_secretary_can_access_unpaid_members(self):
        """Test secretary can access unpaid members tool."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_unpaid_members",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.secretary)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])

    def test_member_cannot_access_unpaid_members(self):
        """Test regular member cannot access unpaid members tool."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_unpaid_members",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        result = response.data["result"]
        self.assertFalse(result.get("available", True))

    def test_auditor_can_access_loan_book(self):
        """Test auditor can access loan book tool."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_loan_book",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.auditor)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])

    def test_member_cannot_access_loan_book(self):
        """Test regular member cannot access loan book."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_loan_book",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        result = response.data["result"]
        self.assertFalse(result.get("available", True))

    def test_member_cannot_access_audit_logs(self):
        """Test regular member cannot access audit logs."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_audit_logs",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.member)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        result = response.data["result"]
        self.assertFalse(result.get("available", True))

    def test_auditor_can_access_audit_logs(self):
        """Test auditor can access audit logs."""
        request = self.factory.post(
            "/api/v1/ai/tool/execute/",
            {
                "tool_name": "get_audit_logs",
                "params": {},
                "chama_id": str(self.chama.id),
            },
            format="json",
        )
        request.META["HTTP_X_CHAMA_ID"] = str(self.chama.id)
        force_authenticate(request, user=self.auditor)

        response = views.ai_tool_execute(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
