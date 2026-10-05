from django.contrib import admin
from django.urls import path

from sheets_reports import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('tableros/<int:dashboard_id>/edit/', views.board_editor, name='board_editor'),
    path('tableros/<int:dashboard_id>/shared/', views.board_view, name='board_view'),
    path('api/dashboards/', views.dashboard_list, name='dashboard_list'),
    path('api/dashboards/<int:dashboard_id>/', views.dashboard_detail, name='dashboard_detail'),
    path('api/dashboards/<int:dashboard_id>/duplicate/', views.dashboard_duplicate, name='dashboard_duplicate'),
    path('api/dashboard/<int:dashboard_id>/schema/', views.dashboard_schema, name='dashboard_schema'),
    path('api/dashboard/<int:dashboard_id>/render/', views.dashboard_render, name='dashboard_render'),
    path('api/dashboard/<int:dashboard_id>/widgets/', views.create_widget, name='create_widget'),
    path('api/dashboard/<int:dashboard_id>/table-assistant/', views.table_assistant, name='table_assistant'),
    path('api/widget/<int:widget_id>/', views.widget_detail, name='widget_detail'),
]
