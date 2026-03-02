from django.urls import path
from . import views_frontend

app_name = 'chama'

urlpatterns = [
    # Chama Management Templates
    path('members/', views_frontend.member_list_view, name='member_list'),
    path('members/<uuid:member_id>/', views_frontend.member_detail_view, name='member_detail'),
    path('join/', views_frontend.join_chama_view, name='join_chama'),
    path('join/pending/', views_frontend.join_pending_view, name='join_pending'),
    path('join/<slug:status_slug>/', views_frontend.join_status_view, name='join_status'),
    path('membership-requests/', views_frontend.membership_requests_view, name='membership_requests'),
    path('settings/', views_frontend.chama_settings_view, name='chama_settings'),
    path('create/', views_frontend.chama_create_view, name='chama_create'),
    # Legacy aliases used by dashboards
    path('settings/legacy/', views_frontend.chama_settings_view, name='settings'),
    path('attendance/', views_frontend.member_list_view, name='attendance'),
    path('register-member/', views_frontend.chama_create_view, name='register_member'),
]
