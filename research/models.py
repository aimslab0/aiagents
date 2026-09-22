from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class ResearchQuery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    chat = models.ForeignKey("chats.Chat", on_delete=models.CASCADE, related_name="research_queries")
    question = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    total_duration_ms = models.PositiveBigIntegerField(null=True, blank=True)
    evaluation_case = models.JSONField(null=True, blank=True)
    submission_key = models.UUIDField(unique=True, null=True, blank=True)
    stage = models.CharField(max_length=30, default="PENDING")
    execution_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    execution_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.question[:80]


class ExecutionMetrics(models.Model):
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    latency_ms = models.PositiveBigIntegerField(null=True, blank=True)
    input_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    output_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    total_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=18, decimal_places=10, null=True, blank=True)
    cost_source = models.CharField(max_length=20, blank=True)
    succeeded = models.BooleanField(null=True, blank=True)

    class Meta:
        abstract = True


class AgentResponse(ExecutionMetrics):
    research_query = models.ForeignKey(ResearchQuery, on_delete=models.CASCADE, related_name="agent_responses")
    provider = models.CharField(max_length=100)
    model_name = models.CharField(max_length=200)
    raw_response = models.TextField()
    normalized_response = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    provider_key = models.CharField(max_length=100, blank=True)
    paper_count = models.PositiveIntegerField(null=True, blank=True)
    citations_used = models.ManyToManyField("Citation", blank=True, related_name="evidence_attempts")

    def __str__(self):
        return f"{self.provider} / {self.model_name}"


class Citation(models.Model):
    research_query = models.ForeignKey(ResearchQuery, on_delete=models.CASCADE, related_name="citations")
    agent_response = models.ForeignKey(AgentResponse, on_delete=models.SET_NULL, null=True, blank=True, related_name="citations")
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=2000)
    source_name = models.CharField(max_length=200)
    published_date = models.DateField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    def clean(self):
        super().clean()
        if self.agent_response_id and self.agent_response.research_query_id != self.research_query_id:
            raise ValidationError({"agent_response": "The agent response must belong to the same research query."})

    def __str__(self):
        return self.title


class SynthesisAttempt(ExecutionMetrics):
    research_query = models.ForeignKey(ResearchQuery, on_delete=models.CASCADE, related_name="synthesis_attempts")
    model_name = models.CharField(max_length=200)
    answer = models.TextField(blank=True)
    confidence = models.FloatField(null=True, blank=True)
    synthesis_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class FinalResponse(models.Model):
    research_query = models.OneToOneField(ResearchQuery, on_delete=models.CASCADE, related_name="final_response")
    answer = models.TextField()
    confidence = models.FloatField(null=True, blank=True)
    synthesis_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    active_attempt = models.ForeignKey(SynthesisAttempt, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Final response for research {self.research_query_id}"


class ResearchAction(models.Model):
    key = models.UUIDField(unique=True)
    research_query = models.ForeignKey(ResearchQuery, on_delete=models.CASCADE, related_name="actions")
    target = models.CharField(max_length=100)
    model_name = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DeveloperRating(models.Model):
    research_query = models.ForeignKey(ResearchQuery, on_delete=models.CASCADE, related_name="ratings")
    synthesis_attempt = models.ForeignKey(SynthesisAttempt, on_delete=models.SET_NULL, null=True, blank=True)
    answer_quality = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    citation_quality = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    evidence_relevance = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    synthesis_quality = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
