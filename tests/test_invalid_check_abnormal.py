from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.routes.admin import build_admin_router


class DummyConfigManager:
    def get(self, key, default=None):
        return default

    def get_all(self):
        return {}


class DummyTokenManager:
    def __init__(self, tokens):
        self.tokens = {str(item["id"]): dict(item) for item in tokens}

    def get_by_id(self, tid):
        token = self.tokens.get(str(tid))
        return dict(token) if token else None

    def set_status(self, tid, status):
        token = self.tokens.get(str(tid))
        if token:
            token["status"] = status

    def report_invalid_by_identity(self, *, token_id="", **_kwargs):
        token = self.tokens.get(str(token_id))
        if not token:
            return None
        previous = str(token.get("status") or "").strip().lower()
        token["status"] = "invalid"
        result = dict(token)
        result["_previous_status"] = previous
        return result


class DummyRefreshManager:
    def __init__(self, account_ids=None):
        self.account_ids = dict(account_ids or {})
        self.disabled_profile_ids = []

    def _extract_account_id(self, token_value):
        return self.account_ids.get(str(token_value), "")

    def set_enabled(self, profile_id, enabled):
        if enabled is False:
            self.disabled_profile_ids.append(str(profile_id))
        return {"id": profile_id, "enabled": bool(enabled)}

    def is_profile_enabled(self, profile_id):
        return str(profile_id) not in self.disabled_profile_ids


class DummyStore:
    def get(self, _code):
        return None

    def list(self, limit=200):
        return []

    def count_in_progress(self):
        return 0


def build_client(token_manager, refresh_manager):
    app = FastAPI()
    app.include_router(
        build_admin_router(
            static_dir=Path("."),
            token_manager=token_manager,
            config_manager=DummyConfigManager(),
            refresh_manager=refresh_manager,
            log_store=DummyStore(),
            error_store=DummyStore(),
            live_log_store=DummyStore(),
            require_admin_auth=lambda request: None,
            is_admin_authenticated=lambda request: True,
            apply_client_config=lambda: None,
            get_generated_storage_stats=lambda: {},
            get_redis_health=lambda: {},
        )
    )
    return TestClient(app)


def test_invalid_check_marks_missing_account_id_token_disabled():
    token_manager = DummyTokenManager(
        [
            {
                "id": "tok-1",
                "value": "token-without-account",
                "status": "active",
                "auto_refresh": True,
                "refresh_profile_id": "profile-1",
            }
        ]
    )
    refresh_manager = DummyRefreshManager()
    client = build_client(token_manager, refresh_manager)

    response = client.post("/api/v1/tokens/check-invalid-batch", json={"ids": ["tok-1"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["abnormal_count"] == 1
    assert payload["disabled_count"] == 1
    assert payload["disabled_auto_refresh_count"] == 1
    assert payload["skipped_count"] == 0
    assert token_manager.get_by_id("tok-1")["status"] == "disabled"
    assert refresh_manager.disabled_profile_ids == ["profile-1"]


def test_invalid_check_disables_error_status_token_and_auto_refresh():
    token_manager = DummyTokenManager(
        [
            {
                "id": "tok-2",
                "value": "token-error",
                "status": "error",
                "auto_refresh": True,
                "refresh_profile_id": "profile-2",
            }
        ]
    )
    refresh_manager = DummyRefreshManager()
    client = build_client(token_manager, refresh_manager)

    response = client.post("/api/v1/tokens/check-invalid-batch", json={"ids": ["tok-2"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["abnormal_count"] == 1
    assert payload["disabled_count"] == 1
    assert payload["disabled_auto_refresh_count"] == 1
    abnormal = payload["abnormal"][0]
    assert abnormal["previous_status"] == "error"
    assert abnormal["status"] == "disabled"
    assert token_manager.get_by_id("tok-2")["status"] == "disabled"
    assert refresh_manager.disabled_profile_ids == ["profile-2"]


def test_invalid_check_keeps_unknown_403_response_as_skipped(monkeypatch):
    token_manager = DummyTokenManager(
        [
            {
                "id": "tok-3",
                "value": "token-403",
                "status": "active",
                "auto_refresh": True,
                "refresh_profile_id": "profile-3",
            }
        ]
    )
    refresh_manager = DummyRefreshManager({"token-403": "account-403"})
    client = build_client(token_manager, refresh_manager)

    class FakeResponse:
        status_code = 403
        text = '{"error":{"message":"Forbidden"}}'

        def json(self):
            return {"error": {"message": "Forbidden"}}

    monkeypatch.setattr("api.routes.admin.requests.get", lambda *args, **kwargs: FakeResponse())

    response = client.post("/api/v1/tokens/check-invalid-batch", json={"ids": ["tok-3"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["abnormal_count"] == 0
    assert payload["disabled_count"] == 0
    assert payload["disabled_auto_refresh_count"] == 0
    assert payload["skipped_count"] == 1
    assert token_manager.get_by_id("tok-3")["status"] == "active"
    assert refresh_manager.disabled_profile_ids == []
