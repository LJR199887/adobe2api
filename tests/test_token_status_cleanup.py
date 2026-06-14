from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.admin import build_admin_router


class DummyConfigManager:
    def get(self, key, default=None):
        return default

    def get_all(self):
        return {}


class DummyTokenManager:
    def __init__(self):
        self.tokens = {
            status: {
                "id": status,
                "status": status,
                "refresh_profile_id": f"profile-{status}",
            }
            for status in ("active", "exhausted", "invalid", "abnormal")
        }

    def list_all(self):
        return [dict(token) for token in self.tokens.values()]

    def remove_many(self, ids):
        deleted = []
        for token_id in ids:
            if self.tokens.pop(str(token_id), None):
                deleted.append(str(token_id))
        return {"deleted_ids": deleted, "missing_ids": []}


class DummyRefreshManager:
    def __init__(self):
        self.profiles = {
            f"profile-{status}" for status in ("active", "exhausted", "invalid", "abnormal")
        }

    def list_profiles(self):
        return [{"id": profile_id} for profile_id in self.profiles]

    def remove_profiles_only(self, ids):
        deleted = []
        for profile_id in ids:
            if profile_id in self.profiles:
                self.profiles.remove(profile_id)
                deleted.append(profile_id)
        return {"deleted_ids": deleted, "missing_ids": []}


class DummyStore:
    def get(self, _code):
        return None

    def list(self, limit=200):
        return []

    def count_in_progress(self):
        return 0


def build_client():
    token_manager = DummyTokenManager()
    refresh_manager = DummyRefreshManager()
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
    return TestClient(app), token_manager, refresh_manager


def test_cleanup_invalid_deletes_only_invalid_token_and_profile():
    client, token_manager, refresh_manager = build_client()

    preview = client.get("/api/v1/tokens/cleanup-invalid/preview").json()
    response = client.post(
        "/api/v1/tokens/cleanup-invalid",
        json={"include_refresh_profiles": True},
    )

    assert preview["token_count"] == 1
    assert preview["refresh_profile_count"] == 1
    assert response.status_code == 200
    assert response.json()["deleted_ids"] == ["invalid"]
    assert "invalid" not in token_manager.tokens
    assert "profile-invalid" not in refresh_manager.profiles
    assert "abnormal" in token_manager.tokens


def test_cleanup_abnormal_deletes_only_abnormal_token_and_profile():
    client, token_manager, refresh_manager = build_client()

    response = client.post(
        "/api/v1/tokens/cleanup-abnormal",
        json={"include_refresh_profiles": True},
    )

    assert response.status_code == 200
    assert response.json()["deleted_ids"] == ["abnormal"]
    assert "abnormal" not in token_manager.tokens
    assert "profile-abnormal" not in refresh_manager.profiles
    assert "invalid" in token_manager.tokens
