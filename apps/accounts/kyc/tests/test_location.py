import pytest
from rest_framework.test import APIClient

from apps.accounts.models import MemberKYC, MemberKYCStatus, User


@pytest.mark.django_db
def test_kyc_location_saves_for_create_chama_with_share_location_true():
    client = APIClient()
    user = User.objects.create_user(phone="0712345678", password="Passw0rd!123", full_name="Test User")
    kyc = MemberKYC.objects.create(
        user=user,
        chama=None,
        onboarding_path="create_chama",
        status=MemberKYCStatus.DRAFT,
        id_number="A1234567",
    )
    client.force_authenticate(user=user)

    res = client.post(
        "/api/v1/kyc/location/",
        {
            "kyc_id": str(kyc.id),
            "share_location": True,
            "latitude": -1.2921,
            "longitude": 36.8219,
            "location_label": "Nairobi, Kenya",
        },
        format="json",
    )

    assert res.status_code == 200
    assert res.data["success"] is True
    kyc.refresh_from_db()
    assert float(kyc.location_latitude) == pytest.approx(-1.2921, rel=0, abs=1e-6)
    assert float(kyc.location_longitude) == pytest.approx(36.8219, rel=0, abs=1e-6)
    assert kyc.location_label == "Nairobi, Kenya"


@pytest.mark.django_db
def test_kyc_location_returns_structured_400_for_missing_latitude():
    client = APIClient()
    user = User.objects.create_user(phone="0712345679", password="Passw0rd!123", full_name="Test User")
    kyc = MemberKYC.objects.create(
        user=user,
        onboarding_path="create_chama",
        status=MemberKYCStatus.DRAFT,
        id_number="A1234568",
    )
    client.force_authenticate(user=user)

    res = client.post(
        "/api/v1/kyc/location/",
        {
            "kyc_id": str(kyc.id),
            "share_location": True,
            "longitude": 36.8219,
        },
        format="json",
    )

    assert res.status_code == 400
    assert res.data["success"] is False
    assert res.data["code"] == "INVALID_LOCATION_PAYLOAD"
    assert "errors" in res.data
    assert "latitude" in res.data["errors"]


@pytest.mark.django_db
def test_kyc_location_accepts_legacy_payload_without_share_location():
    client = APIClient()
    user = User.objects.create_user(phone="0712345680", password="Passw0rd!123", full_name="Test User")
    kyc = MemberKYC.objects.create(
        user=user,
        onboarding_path="create_chama",
        status=MemberKYCStatus.DRAFT,
        id_number="A1234569",
    )
    client.force_authenticate(user=user)

    res = client.post(
        "/api/v1/kyc/location/",
        {
            "kyc_id": str(kyc.id),
            "location_latitude": -1.2921,
            "location_longitude": 36.8219,
            "location_label": "Nairobi, Kenya",
        },
        format="json",
    )

    assert res.status_code == 200
    assert res.data["success"] is True
    kyc.refresh_from_db()
    assert kyc.location_latitude is not None
    assert kyc.location_longitude is not None


@pytest.mark.django_db
def test_kyc_location_allows_share_location_false_and_clears_existing_coords():
    client = APIClient()
    user = User.objects.create_user(phone="0712345681", password="Passw0rd!123", full_name="Test User")
    kyc = MemberKYC.objects.create(
        user=user,
        onboarding_path="create_chama",
        status=MemberKYCStatus.DRAFT,
        id_number="A1234570",
        location_latitude="-1.292100",
        location_longitude="36.821900",
        location_label="Nairobi, Kenya",
    )
    client.force_authenticate(user=user)

    res = client.post(
        "/api/v1/kyc/location/",
        {
            "kyc_id": str(kyc.id),
            "share_location": False,
        },
        format="json",
    )

    assert res.status_code == 200
    assert res.data["success"] is True
    kyc.refresh_from_db()
    assert kyc.location_latitude is None
    assert kyc.location_longitude is None

