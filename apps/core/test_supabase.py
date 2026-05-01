from __future__ import annotations

import json

import pytest
import requests

from core.supabase import SupabaseAdminClient, SupabaseConfig


def _configured_client() -> SupabaseAdminClient:
    return SupabaseAdminClient(
        SupabaseConfig(
            url="https://example.supabase.co",
            service_role_key="service-role-key",
            timeout=5,
            database_host="db.example.supabase.co",
            storage_bucket="mychama",
            storage_public=False,
            signed_url_ttl=3600,
        )
    )


def _response(status_code: int, payload: dict | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://example.supabase.co/storage/v1/object/info/mychama/kyc/id.jpg"
    if payload is not None:
        response._content = json.dumps(payload).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    else:
        response._content = b""
    return response


def test_get_storage_object_info_returns_none_on_404(monkeypatch):
    client = _configured_client()

    def fake_request(*args, **kwargs):
        return _response(404, {"message": "Not found"})

    monkeypatch.setattr("core.supabase.requests.request", fake_request)
    assert client.get_storage_object_info("mychama", "kyc/id.jpg") is None


def test_get_storage_object_info_returns_none_on_400_object_not_found(monkeypatch):
    client = _configured_client()

    def fake_request(*args, **kwargs):
        return _response(400, {"error": "Object not found", "message": "Object not found"})

    monkeypatch.setattr("core.supabase.requests.request", fake_request)
    assert client.get_storage_object_info("mychama", "kyc/id.jpg") is None


def test_get_storage_object_info_returns_none_on_400_resource_not_found(monkeypatch):
    client = _configured_client()

    def fake_request(*args, **kwargs):
        return _response(400, {"error": "Bad Request", "message": "The resource was not found"})

    monkeypatch.setattr("core.supabase.requests.request", fake_request)
    assert client.get_storage_object_info("mychama", "kyc/id.jpg") is None


def test_get_storage_object_info_raises_on_other_400(monkeypatch):
    client = _configured_client()

    def fake_request(*args, **kwargs):
        return _response(400, {"message": "Bucket not found"})

    monkeypatch.setattr("core.supabase.requests.request", fake_request)

    with pytest.raises(requests.HTTPError):
        client.get_storage_object_info("mychama", "kyc/id.jpg")


def test_supabase_storage_exists_returns_false_on_http_400(monkeypatch):
    from core.storage import SupabaseStorage

    def fake_get_config():
        return SupabaseConfig(
            url="https://example.supabase.co",
            service_role_key="service-role-key",
            timeout=5,
            database_host="db.example.supabase.co",
            storage_bucket="mychama",
            storage_public=False,
            signed_url_ttl=3600,
        )

    monkeypatch.setattr("core.storage.get_supabase_config", fake_get_config)
    storage = SupabaseStorage(bucket_name="mychama")

    response = _response(400, {"message": "Object not found"})
    err = requests.HTTPError("bad request", response=response)

    def fake_info(*args, **kwargs):
        raise err

    monkeypatch.setattr(storage.client, "get_storage_object_info", fake_info)
    assert storage.exists("kyc/id.jpg") is False


def test_supabase_storage_exists_returns_false_on_request_exception(monkeypatch):
    from core.storage import SupabaseStorage

    def fake_get_config():
        return SupabaseConfig(
            url="https://example.supabase.co",
            service_role_key="service-role-key",
            timeout=5,
            database_host="db.example.supabase.co",
            storage_bucket="mychama",
            storage_public=False,
            signed_url_ttl=3600,
        )

    monkeypatch.setattr("core.storage.get_supabase_config", fake_get_config)
    storage = SupabaseStorage(bucket_name="mychama")

    def fake_info(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(storage.client, "get_storage_object_info", fake_info)
    assert storage.exists("kyc/id.jpg") is False
