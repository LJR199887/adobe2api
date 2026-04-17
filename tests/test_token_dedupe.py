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
    manager.tokens.append(
        {
            "id": "profile-row",
            "value": "token-old",
            "status": "active",
            "fails": 0,
            "added_at": 1,
            "error_until": 0,
            "source": "auto_refresh",
            "auto_refresh": True,
            "refresh_profile_id": "profile-new",
        }
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
