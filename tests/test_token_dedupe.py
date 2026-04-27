import json

from core import refresh_mgr, token_mgr


def make_token_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(token_mgr, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(token_mgr, "DATA_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(token_mgr, "LEGACY_DATA_FILE", tmp_path / "legacy_tokens.json")
    return token_mgr.TokenManager()


def make_refresh_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_mgr, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(refresh_mgr, "PROFILE_FILE", tmp_path / "refresh_profile.json")
    return refresh_mgr.RefreshManager()


def test_cookie_import_token_overwrites_existing_token(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    original = manager.add("token-A")

    result = manager.upsert_auto_refresh_token(
        "token-A",
        profile_id="profile-new",
        profile_name="Imported Account",
        profile_email="imported@example.com",
    )

    assert result["id"] == original["id"]
    assert len(manager.tokens) == 1
    assert manager.tokens[0]["value"] == "token-A"
    assert manager.tokens[0]["source"] == "auto_refresh"
    assert manager.tokens[0]["auto_refresh"] is True
    assert manager.tokens[0]["refresh_profile_id"] == "profile-new"
    assert manager.tokens[0]["refresh_profile_name"] == "Imported Account"
    assert manager.tokens[0]["refresh_profile_email"] == "imported@example.com"


def test_import_cookie_reuses_existing_profile_for_same_cookie(tmp_path, monkeypatch):
    manager = make_refresh_manager(tmp_path, monkeypatch)
    first = manager.import_cookie("cookie: a=1; b=2", name="First")
    second = manager.import_cookie(
        [{"name": "b", "value": "2"}, {"name": "a", "value": "1"}],
        name="Second",
    )

    assert first["reused_existing_profile"] is False
    assert first["id"] == second["id"]
    assert second["reused_existing_profile"] is True
    assert len(manager.list_profiles()) == 1
    assert manager.list_profiles()[0]["name"] == "Second"


def test_add_marks_duplicate_token(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)

    first = manager.add("token-A")
    second = manager.add("Bearer token-A")

    assert first["_created"] is True
    assert first["_duplicate"] is False
    assert second["_created"] is False
    assert second["_duplicate"] is True
    assert len(manager.tokens) == 1


def test_auto_refresh_upsert_merges_duplicate_value_and_profile(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    existing = manager.add(
        "token-A",
        meta={
            "source": "auto_refresh",
            "auto_refresh": True,
            "refresh_profile_id": "profile-old",
        },
    )
    manager.add(
        "token-old",
        meta={
            "id": "profile-row",
            "source": "auto_refresh",
            "auto_refresh": True,
            "refresh_profile_id": "profile-new",
        },
    )

    result = manager.upsert_auto_refresh_token("token-A", profile_id="profile-new")

    assert result["id"] == existing["id"]
    assert result["_merged_refresh_profile_ids"] == ["profile-old"]
    assert result["_duplicate_token"] is True
    assert len(manager.tokens) == 1
    assert manager.tokens[0]["id"] == existing["id"]
    assert manager.tokens[0]["value"] == "token-A"
    assert manager.tokens[0]["refresh_profile_id"] == "profile-new"


def test_cookie_fingerprint_matches_same_cookie_pairs_in_different_order():
    cookie_text = "b=2; a=1"
    cookie_list = [
        {"name": "a", "value": "1"},
        {"name": "b", "value": "2"},
    ]

    assert refresh_mgr.RefreshManager.cookie_fingerprint(cookie_text)
    assert refresh_mgr.RefreshManager.cookie_fingerprint(cookie_text) == (
        refresh_mgr.RefreshManager.cookie_fingerprint(cookie_list)
    )


def test_refresh_manager_bulk_disables_profiles(tmp_path, monkeypatch):
    manager = make_refresh_manager(tmp_path, monkeypatch)
    first = manager.import_cookie("cookie: a=1", name="First")
    second = manager.import_cookie("cookie: b=2", name="Second")

    result = manager.set_enabled_many([first["id"], second["id"]], False)
    enabled = manager.profiles_enabled([first["id"], second["id"], "missing"])

    assert result["changed"] == 2
    assert enabled[first["id"]] is False
    assert enabled[second["id"]] is False
    assert enabled["missing"] is None


def test_token_manager_migrates_legacy_json_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(token_mgr, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(token_mgr, "DATA_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(token_mgr, "LEGACY_DATA_FILE", tmp_path / "legacy_tokens.json")
    (tmp_path / "tokens.json").write_text(
        json.dumps(
            [
                {
                    "id": "token-1",
                    "value": "token-A",
                    "status": "active",
                    "success_count": 3,
                }
            ]
        ),
        encoding="utf-8",
    )

    manager = token_mgr.TokenManager()

    assert (tmp_path / "app.db").exists()
    assert manager.get_by_id("token-1")["success_count"] == 3

    (tmp_path / "tokens.json").unlink()
    reloaded = token_mgr.TokenManager()

    assert reloaded.get_by_id("token-1")["value"] == "token-A"


def test_refresh_manager_migrates_legacy_json_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_mgr, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(refresh_mgr, "PROFILE_FILE", tmp_path / "refresh_profile.json")
    (tmp_path / "refresh_profile.json").write_text(
        json.dumps(
            {
                "version": 2,
                "profiles": [
                    {
                        "id": "profile-1",
                        "name": "Imported",
                        "enabled": True,
                        "imported_at": 123,
                        "endpoint": {
                            "url": refresh_mgr.RefreshManager.DEFAULT_REFRESH_URL,
                            "method": "POST",
                            "form": {
                                "client_id": "clio-playground-web",
                                "guest_allowed": "true",
                                "scope": refresh_mgr.RefreshManager.DEFAULT_SCOPE,
                            },
                            "headers": {
                                "Cookie": "a=1; b=2",
                                "Accept": "*/*",
                                "Accept-Language": "zh-CN,zh;q=0.9",
                                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                                "Origin": "https://firefly.adobe.com",
                                "Referer": "https://firefly.adobe.com/",
                                "User-Agent": "Mozilla/5.0",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = refresh_mgr.RefreshManager()

    assert (tmp_path / "app.db").exists()
    assert manager.list_profiles()[0]["id"] == "profile-1"

    (tmp_path / "refresh_profile.json").unlink()
    reloaded = refresh_mgr.RefreshManager()

    assert reloaded.list_profiles()[0]["name"] == "Imported"


def test_token_list_page_uses_sqlite_pagination(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    manager.add("token-old", meta={"id": "old", "added_at": 1, "updated_at": 1})
    manager.add("token-new", meta={"id": "new", "added_at": 2, "updated_at": 3})
    manager.add("token-mid", meta={"id": "mid", "added_at": 3, "updated_at": 2})

    page_one = manager.list_page(page=1, page_size=2)
    page_two = manager.list_page(page=2, page_size=2)

    assert page_one["backend"] == "sqlite"
    assert [item["id"] for item in page_one["tokens"]] == ["new", "mid"]
    assert [item["id"] for item in page_two["tokens"]] == ["old"]
    assert page_one["pagination"]["total"] == 3
    assert page_one["pagination"]["total_pages"] == 2


def test_token_list_page_sqlite_filters_status_and_credit_errors(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    manager.add("token-active", meta={"id": "active", "status": "active"})
    manager.add("token-invalid", meta={"id": "invalid", "status": "invalid"})
    manager.add(
        "token-credit-error",
        meta={
            "id": "credit-error",
            "status": "active",
            "credits_error": "credits request failed: 403",
        },
    )

    invalid_page = manager.list_page(status="invalid")
    credits_page = manager.list_page(credits="error")

    assert invalid_page["backend"] == "sqlite"
    assert [item["id"] for item in invalid_page["tokens"]] == ["invalid"]
    assert credits_page["backend"] == "sqlite"
    assert [item["id"] for item in credits_page["tokens"]] == ["credit-error"]
    assert credits_page["summary"]["filtered"] == 1


def test_token_list_page_falls_back_to_memory_if_sqlite_fails(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    manager.add("token-A", meta={"id": "token-A"})

    def fail_sqlite_page(**kwargs):
        raise RuntimeError("sqlite unavailable")

    monkeypatch.setattr(manager._store, "list_tokens_page", fail_sqlite_page)

    payload = manager.list_page(page=1, page_size=50)

    assert payload["backend"] == "memory"
    assert [item["id"] for item in payload["tokens"]] == ["token-A"]
