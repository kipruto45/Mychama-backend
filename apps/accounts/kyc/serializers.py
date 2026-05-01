from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.accounts.models import MemberKYCDocumentType, MemberKYCStatus
from apps.accounts.serializers import MemberKYCSerializer


class KYCStartSerializer(serializers.Serializer):
    onboarding_path = serializers.ChoiceField(
        choices=[
            "create_chama",
            "join_chama",
            "request_to_join",
            "existing_member_update",
        ]
    )
    chama_id = serializers.UUIDField(required=False, allow_null=True)


class KYCDetailsSerializer(serializers.Serializer):
    kyc_id = serializers.UUIDField()
    legal_name = serializers.CharField(max_length=255)
    date_of_birth = serializers.DateField()
    gender = serializers.CharField(max_length=32)
    nationality = serializers.CharField(max_length=64)
    id_number = serializers.CharField(max_length=32)
    document_type = serializers.ChoiceField(choices=MemberKYCDocumentType.choices)
    phone_number = serializers.CharField(max_length=16, required=False, allow_blank=True)


class KYCUploadDocumentSerializer(serializers.Serializer):
    kyc_id = serializers.UUIDField()
    document_role = serializers.ChoiceField(
        choices=["id_front_image", "id_back_image", "proof_of_address_image"]
    )
    file = serializers.FileField()


class KYCUploadSelfieSerializer(serializers.Serializer):
    kyc_id = serializers.UUIDField()
    file = serializers.FileField()
    blink_completed = serializers.BooleanField(default=False)
    head_turn_completed = serializers.BooleanField(default=False)
    smile_completed = serializers.BooleanField(default=False)


class KYCSubmitSerializer(serializers.Serializer):
    kyc_id = serializers.UUIDField()


class KYCResubmitSerializer(KYCSubmitSerializer):
    correction_note = serializers.CharField(required=False, allow_blank=True)


class KYCLocationSerializer(serializers.Serializer):
    kyc_id = serializers.UUIDField()
    share_location = serializers.BooleanField(required=False)
    # New contract
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    # Backwards compatible contract (older mobile clients)
    location_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    location_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    location_label = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        share_location_raw = attrs.get("share_location", None)
        latitude = attrs.get("latitude")
        longitude = attrs.get("longitude")
        if latitude is None and attrs.get("location_latitude") is not None:
            latitude = attrs.get("location_latitude")
        if longitude is None and attrs.get("location_longitude") is not None:
            longitude = attrs.get("location_longitude")

        errors: dict[str, list[str]] = {}

        # Backwards compatible: older clients send coordinates but no share_location.
        if share_location_raw is None:
            if latitude is not None or longitude is not None:
                share_location = True
                attrs["share_location"] = True
            else:
                errors["share_location"] = ["This field is required."]
                raise serializers.ValidationError(errors)
        else:
            share_location = bool(share_location_raw)

        if share_location:
            if latitude is None:
                errors["latitude"] = ["This field is required when share_location is true."]
            if longitude is None:
                errors["longitude"] = ["This field is required when share_location is true."]
        else:
            # Explicitly clear persisted coordinates when a user opts out.
            attrs["location_latitude"] = None
            attrs["location_longitude"] = None
            return attrs

        if errors:
            raise serializers.ValidationError(errors)

        # Bounds validation
        if latitude is not None:
            if latitude < Decimal("-90") or latitude > Decimal("90"):
                errors["latitude"] = ["Latitude must be between -90 and 90."]
        if longitude is not None:
            if longitude < Decimal("-180") or longitude > Decimal("180"):
                errors["longitude"] = ["Longitude must be between -180 and 180."]

        if errors:
            raise serializers.ValidationError(errors)

        attrs["location_latitude"] = latitude
        attrs["location_longitude"] = longitude
        return attrs


class KYCStatusResponseSerializer(serializers.Serializer):
    record = MemberKYCSerializer()
    access = serializers.DictField()


class KYCReTriggerSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


def kyc_response(
    *,
    success: bool,
    code: str,
    message: str,
    data: dict | None = None,
    errors: dict | None = None,
):
    return {
        "success": success,
        "code": code,
        "message": message,
        "errors": errors or {},
        "data": data or {},
    }
