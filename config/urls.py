from django.contrib import admin
from django.urls import path

from sheets_reports import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('tableros/nuevo/', views.board_new, name='board_new'),
    path('tableros/<int:dashboard_id>/edit/', views.board_editor, name='board_editor'),
    path('tableros/<int:dashboard_id>/shared/', views.board_view, name='board_view'),
    path('api/dashboards/', views.dashboard_list, name='dashboard_list'),
    path('api/dashboards/<int:dashboard_id>/', views.dashboard_detail, name='dashboard_detail'),
    path('api/dashboards/<int:dashboard_id>/duplicate/', views.dashboard_duplicate, name='dashboard_duplicate'),
    path('api/dashboard/<int:dashboard_id>/sources/', views.dashboard_sources, name='dashboard_sources'),
    path('api/dashboard/<int:dashboard_id>/render/', views.dashboard_render, name='dashboard_render'),
    path('api/dashboard/<int:dashboard_id>/widgets/', views.create_widget, name='create_widget'),
    path('api/dashboard/<int:dashboard_id>/table-assistant/', views.table_assistant, name='table_assistant'),
    path('api/dashboard/<int:dashboard_id>/widget-suggestions/', views.widget_suggestions, name='widget_suggestions'),
    path('api/sources/google/spreadsheets/', views.source_spreadsheets, name='source_spreadsheets'),
    path('api/sources/google/spreadsheets/<str:spreadsheet_id>/tabs/', views.source_tabs, name='source_tabs'),
    path('api/sources/google/spreadsheets/<str:spreadsheet_id>/tabs/<str:gid>/columns/',
         views.source_columns, name='source_columns'),
    path('api/sources/<int:source_id>/', views.source_detail, name='source_detail'),
    path('api/sources/<int:source_id>/columns/', views.source_saved_columns, name='source_saved_columns'),
    path('api/sources/<int:source_id>/schema/', views.source_schema, name='source_schema'),
    path('api/widget/<int:widget_id>/', views.widget_detail, name='widget_detail'),
]
