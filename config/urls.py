"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path

from sheets_reports import views
from sheets_reports import views_dashboard

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('tableros/<slug:board_slug>/edit/', views.board_editor, name='board_editor'),
    path('tableros/<slug:board_slug>/shared/', views.board_view, name='board_view'),
    path('api/dashboards/generate-from-prompt/', views_dashboard.generate_dashboard_from_prompt, name='generate_dashboard_from_prompt'),
    path('api/dashboards/generate-from-prompt/<str:job_id>/', views_dashboard.generate_dashboard_status, name='generate_dashboard_status'),
    path('api/dashboards/', views_dashboard.dashboard_list, name='dashboard_list'),
    path('api/dashboards/<int:dashboard_id>/', views_dashboard.dashboard_detail, name='dashboard_detail'),
    path('api/dashboards/<int:dashboard_id>/duplicate/', views_dashboard.dashboard_duplicate, name='dashboard_duplicate'),
    path('api/dashboard/<int:dashboard_id>/widgets/', views.dashboard_widgets, name='dashboard_widgets'),
    path('api/widget/<int:widget_id>/', views.widget_detail, name='widget_detail'),
    path('api/widget/<int:widget_id>/data/', views.widget_data, name='widget_data'),
    path('api/dashboard/<int:dashboard_id>/generate-widget-code/', views.generate_widget_code, name='generate_widget_code'),
    path('api/dashboard/<int:dashboard_id>/utils/', views.dashboard_util_functions, name='dashboard_util_functions'),
    path('api/dashboard/<int:dashboard_id>/utils/generate/', views.generate_custom_util, name='generate_custom_util'),
    path('api/util-function/<int:util_id>/', views.util_function_detail, name='util_function_detail'),
    path('api/dashboard/<int:dashboard_id>/tables/', views.dashboard_tables, name='dashboard_tables'),
    path('api/dashboard/<int:dashboard_id>/calculated-columns/', views.dashboard_calculated_columns, name='dashboard_calculated_columns'),
    path('api/dashboard/<int:dashboard_id>/calculated-columns/generate/', views.generate_calculated_column, name='generate_calculated_column'),
    path('api/calculated-column/<int:cc_id>/', views.calculated_column_detail, name='calculated_column_detail'),
]
