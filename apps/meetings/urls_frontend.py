from django.urls import path

from . import views_frontend

app_name = 'meetings'

urlpatterns = [
    # Meetings Templates
    path('', views_frontend.meeting_list_view, name='meeting_list'),
    path('<uuid:meeting_id>/', views_frontend.meeting_detail_view, name='meeting_detail'),
    path('create/', views_frontend.meeting_create_view, name='meeting_create'),
    # Legacy aliases used by dashboards
    path('schedule/', views_frontend.meeting_list_view, name='schedule'),
    path('minutes/', views_frontend.meeting_list_view, name='minutes'),
    path('agenda/', views_frontend.meeting_list_view, name='agenda'),
    path('take-minutes/', views_frontend.meeting_create_view, name='take_minutes'),
]
