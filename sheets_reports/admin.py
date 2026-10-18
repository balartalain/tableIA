import json

from django.contrib import admin
from django.utils.html import format_html

from sheets_reports.models import (
    Dashboard, DataSource, EmailCampaign, EmailLog, EmailRecipient, EmailSender, TaskRun, Widget,
)


def _pretty_json(value):
    return format_html("<pre style='margin:0'>{}</pre>", json.dumps(value, indent=2, ensure_ascii=False))


class DataSourceInline(admin.TabularInline):
    model = DataSource
    extra = 0
    fields = ["sheet_name", "tab_name", "sheet_id", "gid"]


class WidgetInline(admin.TabularInline):
    model = Widget
    extra = 0
    fields = ["type", "source", "position", "source_prompt", "updated_at"]
    readonly_fields = ["type", "source_prompt", "updated_at"]
    show_change_link = True


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display = ["nombre", "owner", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["nombre", "sources__sheet_name"]
    inlines = [DataSourceInline, WidgetInline]


@admin.register(Widget)
class WidgetAdmin(admin.ModelAdmin):
    list_display = ["__str__", "dashboard", "type", "updated_at"]
    list_filter = ["type", "updated_at"]
    search_fields = ["source_prompt"]
    fields = ["dashboard", "source", "type", "position", "source_prompt", "fields_pretty", "style_pretty", "created_at", "updated_at"]
    readonly_fields = ["type", "source_prompt", "fields_pretty", "style_pretty", "created_at", "updated_at"]

    @admin.display(description="fields")
    def fields_pretty(self, obj):
        return _pretty_json(obj.fields)

    @admin.display(description="style")
    def style_pretty(self, obj):
        return _pretty_json(obj.style)


class EmailSenderInline(admin.TabularInline):
    model = EmailSender
    extra = 0
    # La contraseña de aplicación está cifrada: no se muestra ni se edita aquí.
    fields = ["email"]


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = ["dashboard", "enabled", "frequency", "send_time", "daily_limit", "batch_size", "last_run_on"]
    list_filter = ["enabled", "frequency"]
    readonly_fields = ["running_since"]
    inlines = [EmailSenderInline]


@admin.register(EmailRecipient)
class EmailRecipientAdmin(admin.ModelAdmin):
    list_display = ["email", "campaign", "status", "attempts", "sent_at", "sender_email"]
    list_filter = ["status"]
    search_fields = ["email"]


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "campaign", "level", "message", "recipient_email", "sender_email"]
    list_filter = ["level"]
    search_fields = ["message", "recipient_email"]


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    list_display = ["label", "started_at", "duration", "status_display", "detail"]
    list_filter = ["label", "status"]
    readonly_fields = ["label", "started_at", "finished_at", "status", "detail", "duration"]

    @admin.display(description="Duración")
    def duration(self, obj):
        return obj.duration or "—"

    @admin.display(description="Estado", ordering="status")
    def status_display(self, obj):
        return obj.get_status_display()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return False
