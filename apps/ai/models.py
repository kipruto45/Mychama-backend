from django.conf import settings
from django.db import models

from apps.ai.fields import EmbeddingVectorField
from core.models import BaseModel


class AIConversationMode(models.TextChoices):
    PUBLIC = "public", "Public"
    PRIVATE = "private", "Private"
    MEMBER_ASSISTANT = "member_assistant", "Member Assistant"
    ADMIN_ASSISTANT = "admin_assistant", "Admin Assistant"
    REPORT_EXPLAINER = "report_explainer", "Report Explainer"
    ISSUE_TRIAGE = "issue_triage", "Issue Triage"


class AIMessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"
    TOOL = "tool", "Tool"


class KnowledgeSourceType(models.TextChoices):
    POLICY = "policy", "Policy"
    MEETING_MINUTES = "meeting_minutes", "Meeting Minutes"
    CONSTITUTION = "constitution", "Constitution"
    OTHER = "other", "Other"


class RiskLevel(models.TextChoices):
    LOW = "low", "Low Risk"
    MEDIUM = "medium", "Medium Risk"
    HIGH = "high", "High Risk"


class FraudSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class FraudType(models.TextChoices):
    RAPID_WITHDRAWAL = "rapid_withdrawal", "Rapid Withdrawal After Deposit"
    STK_FAILURES = "stk_failures", "Multiple STK Failures"
    UNUSUAL_LOAN_PATTERN = "unusual_loan_pattern", "Unusual Loan Pattern"
    DEVICE_CHANGE = "device_change", "Rapid Device Token Changes"
    UNUSUAL_AMOUNT = "unusual_amount", "Unusual Transaction Amount"
    OTHER = "other", "Other"


class AIConversation(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_conversations",
        null=True,
        blank=True,
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ai_conversations",
        null=True,
        blank=True,
    )
    mode = models.CharField(
        max_length=40,
        choices=AIConversationMode.choices,
        db_index=True,
    )
    title = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chama", "user", "-created_at"],
                name="ai_aiconver_chama_i_ed4d01_idx",
            ),
            models.Index(
                fields=["mode", "-created_at"],
                name="ai_aiconver_mode_fd97b1_idx",
            ),
            models.Index(
                fields=["chama", "-created_at"],
                name="ai_aiconver_chama_i_9f17bc_idx",
            ),
        ]


class AIMessage(BaseModel):
    conversation = models.ForeignKey(
        AIConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=AIMessageRole.choices, db_index=True)
    content = models.TextField()
    tool_name = models.CharField(max_length=120, blank=True, db_index=True)
    tool_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["conversation", "-created_at"],
                name="ai_aimessag_convers_1df62d_idx",
            ),
            models.Index(
                fields=["role", "-created_at"],
                name="ai_aimessag_role_3208cc_idx",
            ),
        ]


class AIToolCallLog(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ai_tool_call_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_tool_call_logs",
    )
    tool_name = models.CharField(max_length=120, db_index=True)
    args = models.JSONField(default=dict, blank=True)
    result_summary = models.TextField(blank=True)
    allowed = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chama", "tool_name", "-created_at"],
                name="ai_aitoolca_chama_i_ab7bea_idx",
            ),
            models.Index(
                fields=["actor", "-created_at"],
                name="ai_aitoolca_actor_i_8288a4_idx",
            ),
        ]


class KnowledgeDocument(BaseModel):
    title = models.CharField(max_length=255, db_index=True)
    source_type = models.CharField(
        max_length=40,
        choices=KnowledgeSourceType.choices,
        default=KnowledgeSourceType.OTHER,
        db_index=True,
    )
    text_content = models.TextField(blank=True)
    file = models.FileField(upload_to="ai_knowledge/", null=True, blank=True)
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="knowledge_documents",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chama", "source_type", "-created_at"],
                name="ai_knowledg_chama_i_8aa4e7_idx",
            ),
            models.Index(
                fields=["chama", "-created_at"],
                name="ai_knowledg_chama_i_35d228_idx",
            ),
        ]


class KnowledgeChunk(BaseModel):
    document = models.ForeignKey(
        KnowledgeDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_text = models.TextField()
    embedding_vector = EmbeddingVectorField(dimensions=1536, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["document", "-created_at"],
                name="ai_knowledg_documen_2ebe23_idx",
            ),
        ]


class AIActionLog(BaseModel):
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ai_action_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_action_logs",
    )
    action_type = models.CharField(max_length=80, db_index=True)
    references = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=120, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chama", "action_type", "-created_at"],
                name="ai_aiaction_chama_i_60b56c_idx",
            ),
            models.Index(
                fields=["actor", "-created_at"],
                name="ai_aiaction_actor_i_3ebb55_idx",
            ),
        ]


class AIInteraction(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_interactions",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ai_interactions",
        null=True,
        blank=True,
    )
    question = models.TextField()
    response = models.TextField()
    context_data = models.JSONField(default=dict, blank=True)
    helpful = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "created_at"],
                name="ai_aiintera_user_id_327664_idx",
            ),
            models.Index(
                fields=["chama", "created_at"],
                name="ai_aiintera_chama_i_236716_idx",
            ),
        ]

    def __str__(self):
        return f"AI Chat: {self.user_id} - {self.question[:50]}"


class RiskProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="risk_profile",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="risk_profiles",
    )
    risk_score = models.PositiveIntegerField(default=50)
    risk_level = models.CharField(
        max_length=20,
        choices=RiskLevel.choices,
        default=RiskLevel.MEDIUM,
    )
    contribution_consistency_score = models.PositiveIntegerField(default=50)
    payment_history_score = models.PositiveIntegerField(default=50)
    debt_ratio = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    withdrawal_frequency_score = models.PositiveIntegerField(default=50)
    loan_multiplier = models.DecimalField(max_digits=3, decimal_places=1, default=2.0)
    last_calculated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["chama", "risk_level"],
                name="ai_riskprof_chama_i_91aafa_idx",
            ),
            models.Index(
                fields=["chama", "-risk_score"],
                name="ai_riskprof_chama_i_c7a540_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "chama"),
                name="uniq_risk_profile_per_user_chama",
            ),
            models.CheckConstraint(
                condition=models.Q(risk_score__gte=0, risk_score__lte=100),
                name="risk_score_bounds",
            ),
        ]

    def __str__(self):
        return f"Risk: {self.user_id} - {self.risk_level} ({self.risk_score})"


class FraudFlag(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fraud_flags",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="fraud_flags",
    )
    fraud_type = models.CharField(max_length=30, choices=FraudType.choices)
    severity = models.CharField(max_length=20, choices=FraudSeverity.choices)
    description = models.TextField()
    evidence = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_fraud_flags",
    )
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "resolved"],
                name="ai_fraudfla_user_id_625a48_idx",
            ),
            models.Index(
                fields=["chama", "resolved"],
                name="ai_fraudfla_chama_i_755849_idx",
            ),
            models.Index(
                fields=["severity", "resolved"],
                name="ai_fraudfla_severit_d2e993_idx",
            ),
        ]

    def __str__(self):
        return f"Fraud: {self.fraud_type} - {self.severity} - {self.resolved}"


class AIInsight(BaseModel):
    INSIGHT_TYPES = [
        ("contribution_trend", "Contribution Trend"),
        ("payment_prediction", "Payment Prediction"),
        ("fund_projection", "Fund Projection"),
        ("member_behavior", "Member Behavior"),
        ("loan_risk", "Loan Risk"),
    ]

    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ai_insights",
    )
    insight_type = models.CharField(max_length=30, choices=INSIGHT_TYPES)
    title = models.CharField(max_length=200)
    description = models.TextField()
    chart_data = models.JSONField(default=dict, blank=True)
    recommendations = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["chama", "insight_type"],
                name="ai_aiinsigh_chama_i_b4a987_idx",
            ),
            models.Index(
                fields=["chama", "is_active"],
                name="ai_aiinsigh_chama_i_403efa_idx",
            ),
        ]

    def __str__(self):
        return f"{self.insight_type}: {self.title}"


class LoanEligibility(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loan_eligibilities",
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="loan_eligibilities",
    )
    max_loan_amount = models.DecimalField(max_digits=12, decimal_places=2)
    suggested_amount = models.DecimalField(max_digits=12, decimal_places=2)
    eligible = models.BooleanField(default=True)
    ineligibility_reason = models.TextField(blank=True)
    risk_factors = models.JSONField(default=list, blank=True)
    suggested_term_months = models.PositiveIntegerField(default=6)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "chama"],
                name="ai_loanelig_user_id_5ca1ed_idx",
            ),
            models.Index(
                fields=["chama", "eligible"],
                name="ai_loanelig_chama_i_2caee0_idx",
            ),
        ]

    def __str__(self):
        return f"Eligibility: {self.user_id} - KES {self.max_loan_amount}"


class AIUsageLog(BaseModel):
    """Track AI API usage for billing and monitoring."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_usage_logs",
        null=True,
        blank=True,
    )
    chama = models.ForeignKey(
        "chama.Chama",
        on_delete=models.CASCADE,
        related_name="ai_usage_logs",
        null=True,
        blank=True,
    )
    conversation = models.ForeignKey(
        AIConversation,
        on_delete=models.SET_NULL,
        related_name="usage_logs",
        null=True,
        blank=True,
    )
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    model_name = models.CharField(max_length=100, blank=True)
    endpoint = models.CharField(max_length=100, db_index=True)
    status_code = models.PositiveSmallIntegerField(default=200)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="ai_aiusaglo_user_id_idx",
            ),
            models.Index(
                fields=["chama", "-created_at"],
                name="ai_aiusaglo_chama_i_idx",
            ),
            models.Index(
                fields=["endpoint", "-created_at"],
                name="ai_aiusaglo_endpoint_idx",
            ),
        ]

    def __str__(self):
        return f"AI Usage: {self.user_id} - {self.endpoint} - {self.tokens_in + self.tokens_out} tokens"


class AIAnswerFeedback(BaseModel):
    """Feedback on AI assistant answers for quality improvement."""

    RATING_THUMBS_UP = "thumbs_up"
    RATING_THUMBS_DOWN = "thumbs_down"

    RATING_CHOICES = [
        (RATING_THUMBS_UP, "Thumbs Up"),
        (RATING_THUMBS_DOWN, "Thumbs Down"),
    ]

    message = models.ForeignKey(
        AIMessage,
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_feedbacks",
    )
    rating = models.CharField(max_length=20, choices=RATING_CHOICES)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["message", "-created_at"],
                name="ai_aifeedb_message_i_idx",
            ),
            models.Index(
                fields=["user", "-created_at"],
                name="ai_aifeedb_user_id_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("message", "user"),
                name="unique_feedback_per_message_user",
            ),
        ]

    def __str__(self):
        return f"Feedback: {self.message_id} - {self.rating}"


class AIAttachment(BaseModel):
    """Attachments (images, files) for AI chat messages."""

    TYPE_IMAGE = "image"
    TYPE_FILE = "file"

    TYPE_CHOICES = [
        (TYPE_IMAGE, "Image"),
        (TYPE_FILE, "File"),
    ]

    message = models.ForeignKey(
        AIMessage,
        on_delete=models.CASCADE,
        related_name="attachments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_attachments",
    )
    attachment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    file_name = models.CharField(max_length=255)
    file_url = models.URLField(max_length=500)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="ai_attach_user_idx"),
            models.Index(fields=["message", "-created_at"], name="ai_attach_msg_idx"),
        ]

    def __str__(self):
        return f"Attachment: {self.file_name} ({self.attachment_type})"
