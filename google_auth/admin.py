from django.contrib import admin

from google_auth.models import GoogleCredential


@admin.register(GoogleCredential)
class GoogleCredentialAdmin(admin.ModelAdmin):
    list_display = ("user", "email", "updated_at")
    search_fields = ("user__username", "email")
    readonly_fields = ("created_at", "updated_at")
