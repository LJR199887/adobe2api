from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes.admin import build_admin_router


class DummyConfigManager:
    def __init__(self, automation_import_key="import-secret"):
        self.automation_import_key = automation_import_key

    def get(self, key, default=None):
        if key == "automation_import_key":
            return self.automation_import_key
        return default

    def get_all(self):
        return {"automation_import_key": self.automation_import_key}


class DummyRefreshManager:
    def __init__(self):
        self.imports = []
        self.refreshes = []

    def import_cookie(self, cookie, name=None):
        self.imports.append({"cookie": cookie, "name": name})
        if not cookie:
            raise ValueError("cookie is required")
        return {
            "id": "profile-1",
            "name": name or "Imported",
            "reused_existing_profile": False,
            "account": {"email": "account@example.com"},
        }

    def refresh_once(self, profile_id, refresh_credits=True):
        self.refreshes.append(
            {"profile_id": profile_id, "refresh_credits": refresh_credits}
        )
        return {
            "status": "ok",
            "profile_id": profile_id,
            "profile_name": "Imported",
            "profile_email": "account@example.com",
            "token_created": True,
            "token_duplicate": False,
            "credits_skipped": not refresh_credits,
            "timing": {},
        }


class DummyStore:
    def get(self, _code):
        return None

    def list(self, limit=200):
        return []

    def count_in_progress(self):
        return 0


class DummyTokenManager:
    pass


def fail_admin_auth(_request):
    raise HTTPException(status_code=401, detail="admin auth should not run")


def build_client(config_manager, refresh_manager):
    app = FastAPI()
    app.include_router(
        build_admin_router(
            static_dir=Path("."),
            token_manager=DummyTokenManager(),
            config_manager=config_manager,
            refresh_manager=refresh_manager,
            log_store=DummyStore(),
            error_store=DummyStore(),
            live_log_store=DummyStore(),
            require_admin_auth=fail_admin_auth,
            is_admin_authenticated=lambda request: False,
            apply_client_config=lambda: None,
            get_generated_storage_stats=lambda: {},
            get_redis_health=lambda: {},
        )
    )
    return TestClient(app)


def test_automation_import_cookie_uses_token_pool_key_without_admin_session():
    refresh_manager = DummyRefreshManager()
    client = build_client(DummyConfigManager(), refresh_manager)

    response = client.post(
        "/api/v1/automation/import-cookie",
        headers={"Authorization": "Bearer import-secret"},
        json={"name": "Account A", "cookie": "a=1; b=2"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["token_added"] is True
    assert payload["token_duplicate"] is False
    assert payload["profile_id"] == "profile-1"
    assert refresh_manager.imports == [{"cookie": "a=1; b=2", "name": "Account A"}]
    assert refresh_manager.refreshes == [
        {"profile_id": "profile-1", "refresh_credits": False}
    ]
    assert payload["refresh_result"]["credits_skipped"] is True


def test_automation_import_cookie_rejects_wrong_key():
    client = build_client(DummyConfigManager(), DummyRefreshManager())

    response = client.post(
        "/api/v1/automation/import-cookie",
        headers={"X-Token-Pool-Key": "wrong"},
        json={"cookie": "a=1"},
    )

    assert response.status_code == 401


def test_automation_import_cookie_is_disabled_without_configured_key():
    client = build_client(
        DummyConfigManager(automation_import_key=""),
        DummyRefreshManager(),
    )

    response = client.post(
        "/api/v1/automation/import-cookie",
        headers={"Authorization": "Bearer anything"},
        json={"cookie": "a=1"},
    )

    assert response.status_code == 403
