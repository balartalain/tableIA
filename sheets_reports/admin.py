import json

from django.contrib import admin
from django.utils.html import format_html

from sheets_reports.models import Dashboard, DataSource, Widget


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
