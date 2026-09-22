from django.contrib import admin

from .models import Chat, Message


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ["title", "created_at", "updated_at"]
    search_fields = ["title"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["chat", "role", "created_at"]
    list_filter = ["role"]
    search_fields = ["content", "chat__title"]
    readonly_fields = ["created_at"]
