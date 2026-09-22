from django.contrib import admin

from .models import AgentResponse, Citation, FinalResponse, ResearchQuery, SynthesisAttempt, ResearchAction, DeveloperRating

admin.site.register(SynthesisAttempt)
admin.site.register(ResearchAction)
admin.site.register(DeveloperRating)


@admin.register(ResearchQuery)
class ResearchQueryAdmin(admin.ModelAdmin):
    list_display = ["id", "chat", "status", "created_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["question"]
    readonly_fields = ["created_at"]


@admin.register(AgentResponse)
class AgentResponseAdmin(admin.ModelAdmin):
    list_display = ["id", "research_query", "provider", "model_name", "created_at"]
    list_filter = ["provider"]
    search_fields = ["model_name", "raw_response"]
    readonly_fields = ["created_at"]


@admin.register(Citation)
class CitationAdmin(admin.ModelAdmin):
    list_display = ["title", "source_name", "research_query", "published_date"]
    search_fields = ["title", "source_name", "url"]


@admin.register(FinalResponse)
class FinalResponseAdmin(admin.ModelAdmin):
    list_display = ["id", "research_query", "confidence", "created_at"]
    search_fields = ["answer"]
    readonly_fields = ["created_at"]
