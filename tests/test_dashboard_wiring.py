import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.chama.models import Chama, Membership, MembershipRole, MemberStatus
from apps.notifications.models import Notification, NotificationType

pytestmark = pytest.mark.django_db


_PHONE_COUNTER = 770100000


def _next_phone() -> str:
    global _PHONE_COUNTER
    _PHONE_COUNTER += 1
    return f"+254{_PHONE_COUNTER}"


def create_user(
    phone: str | None = None,
    full_name: str = "User",
    *,
    is_superuser: bool = False,
) -> User:
    return User.objects.create_user(
        phone=phone or _next_phone(),
        password="SecurePass123!",
        full_name=full_name,
        is_superuser=is_superuser,
        is_staff=is_superuser,
    )


def create_membership(user: User, chama: Chama, role: str, actor: User):
    Membership.objects.create(
        user=user,
        chama=chama,
        role=role,
        status=MemberStatus.ACTIVE,
        is_active=True,
        is_approved=True,
        approved_by=actor,
        created_by=actor,
        updated_by=actor,
    )


def create_seeded_chama_with_roles(seed_label: str):
    actor = create_user(full_name=f"Actor {seed_label}")
    chama = Chama.objects.create(
        name=f"Dashboard Wiring Chama {seed_label}",
        created_by=actor,
        updated_by=actor,
    )
    roles = [
        MembershipRole.MEMBER,
        MembershipRole.TREASURER,
        MembershipRole.SECRETARY,
        MembershipRole.AUDITOR,
        MembershipRole.CHAMA_ADMIN,
    ]
    users = {"actor": actor}
    for role in roles:
        user = create_user(full_name=f"{seed_label} {role.title()}")
        users[role] = user
        create_membership(user, chama, role, actor)
    return chama, users


def test_dashboard_home_routes_member_by_role(client):
    admin = create_user("+254711770001", "Admin")
    member = create_user("+254711770002", "Member")
    chama = Chama.objects.create(
        name="Dashboard Wiring Chama 1",
        created_by=admin,
        updated_by=admin,
    )
    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(member, chama, MembershipRole.MEMBER, admin)

    client.force_login(member)
    response = client.get(reverse("dashboards:home"))
    assert response.status_code == 302
    assert response.url == reverse("dashboards:member_dashboard")


def test_dashboard_home_routes_treasurer(client):
    admin = create_user("+254711770003", "Admin")
    treasurer = create_user("+254711770004", "Treasurer")
    chama = Chama.objects.create(
        name="Dashboard Wiring Chama 2",
        created_by=admin,
        updated_by=admin,
    )
    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(treasurer, chama, MembershipRole.TREASURER, admin)

    client.force_login(treasurer)
    response = client.get(reverse("dashboards:home"))
    assert response.status_code == 302
    assert response.url == reverse("dashboards:treasurer_dashboard")


def test_dashboard_home_routes_superadmin(client):
    superadmin = create_user(
        "+254711770005",
        "Superadmin",
        is_superuser=True,
    )
    client.force_login(superadmin)
    response = client.get(reverse("dashboards:home"))
    assert response.status_code == 302
    assert response.url == reverse("dashboards:superadmin_dashboard")


def test_frontend_logout_and_security_center_routes(client):
    admin = create_user("+254711770006", "Admin")
    member = create_user("+254711770007", "Member")
    chama = Chama.objects.create(
        name="Dashboard Wiring Chama 3",
        created_by=admin,
        updated_by=admin,
    )
    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(member, chama, MembershipRole.MEMBER, admin)

    client.force_login(member)
    security_center_response = client.get(reverse("auth:security_center"))
    assert security_center_response.status_code == 200

    logout_response = client.get(reverse("auth:logout"))
    assert logout_response.status_code == 302
    assert logout_response.url == reverse("auth:login")


def test_member_dashboard_context_contains_switcher_and_notification_count(client):
    admin = create_user("+254711770008", "Admin")
    member = create_user("+254711770009", "Member")
    chama = Chama.objects.create(
        name="Dashboard Wiring Chama 4",
        created_by=admin,
        updated_by=admin,
    )
    create_membership(admin, chama, MembershipRole.CHAMA_ADMIN, admin)
    create_membership(member, chama, MembershipRole.MEMBER, admin)

    Notification.objects.create(
        chama=chama,
        recipient=member,
        type=NotificationType.SYSTEM,
        message="Unread notification for dashboard badge.",
        created_by=admin,
        updated_by=admin,
    )

    client.force_login(member)
    response = client.get(reverse("dashboards:member_dashboard"))
    assert response.status_code == 200
    assert response.context["active_chama_id"] == str(chama.id)
    assert response.context["unread_notifications_count"] == 1
    assert len(response.context["chama_switch_options"]) == 1


@pytest.mark.parametrize(
    "route_name",
    [
        "dashboards:treasurer_dashboard",
        "dashboards:secretary_dashboard",
        "dashboards:auditor_dashboard",
        "dashboards:chama_admin_dashboard",
        "dashboards:superadmin_dashboard",
    ],
)
def test_member_cannot_access_restricted_dashboards(client, route_name):
    chama, users = create_seeded_chama_with_roles("Restricted Access")
    assert chama is not None
    member_user = users[MembershipRole.MEMBER]
    client.force_login(member_user)

    response = client.get(reverse(route_name))
    assert response.status_code == 403


def test_chama_admin_can_access_admin_dashboards(client):
    _chama, users = create_seeded_chama_with_roles("Admin Access")
    admin_user = users[MembershipRole.CHAMA_ADMIN]
    client.force_login(admin_user)

    for route_name in [
        "dashboards:chama_admin_dashboard",
        "dashboards:treasurer_dashboard",
        "dashboards:secretary_dashboard",
        "dashboards:auditor_dashboard",
    ]:
        response = client.get(reverse(route_name))
        assert response.status_code == 200


@pytest.mark.parametrize(
    ("role", "route_name"),
    [
        (MembershipRole.MEMBER, "dashboards:member_dashboard"),
        (MembershipRole.TREASURER, "dashboards:treasurer_dashboard"),
        (MembershipRole.SECRETARY, "dashboards:secretary_dashboard"),
        (MembershipRole.AUDITOR, "dashboards:auditor_dashboard"),
        (MembershipRole.CHAMA_ADMIN, "dashboards:chama_admin_dashboard"),
    ],
)
def test_dashboard_context_keys_available_for_role_dashboards(client, role, route_name):
    _chama, users = create_seeded_chama_with_roles(f"Context {role}")
    user = users[role]
    client.force_login(user)

    response = client.get(reverse(route_name))
    assert response.status_code == 200
    for key in [
        "chama_switch_options",
        "active_chama_id",
        "unread_notifications_count",
    ]:
        assert key in response.context


def test_superadmin_dashboard_context_keys(client):
    superadmin = create_user(full_name="Context Superadmin", is_superuser=True)
    client.force_login(superadmin)
    response = client.get(reverse("dashboards:superadmin_dashboard"))

    assert response.status_code == 200
    for key in [
        "chama_switch_options",
        "active_chama_id",
        "unread_notifications_count",
    ]:
        assert key in response.context


@pytest.mark.parametrize(
    ("role", "route_name", "expected_links"),
    [
        (
            MembershipRole.MEMBER,
            "dashboards:member_dashboard",
            [
                "finance:contributions",
                "payments:transactions_my",
                "notifications:popup",
            ],
        ),
        (
            MembershipRole.TREASURER,
            "dashboards:treasurer_dashboard",
            [
                "payments:loan_disbursements_queue",
                "finance:record_contribution",
                "notifications:popup",
            ],
        ),
        (
            MembershipRole.SECRETARY,
            "dashboards:secretary_dashboard",
            [
                "meetings:meeting_create",
                "notifications:create_announcement",
                "notifications:popup",
            ],
        ),
        (
            MembershipRole.CHAMA_ADMIN,
            "dashboards:chama_admin_dashboard",
            ["issues:issue-list", "finance:loan_application", "notifications:send"],
        ),
        (
            MembershipRole.AUDITOR,
            "dashboards:auditor_dashboard",
            ["reports:activity_log", "payments:admin_transactions"],
        ),
    ],
)
def test_dashboard_cta_links_rendered(client, role, route_name, expected_links):
    _chama, users = create_seeded_chama_with_roles(f"CTA {role}")
    user = users[role]
    client.force_login(user)
    response = client.get(reverse(route_name))

    assert response.status_code == 200
    html = response.content.decode("utf-8")
    for link_name in expected_links:
        assert reverse(link_name) in html
