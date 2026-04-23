from core.stores import ErrorDetailRecord, ErrorDetailStore, RequestLogStore
from core import token_mgr


def make_token_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(token_mgr, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(token_mgr, "DATA_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(token_mgr, "LEGACY_DATA_FILE", tmp_path / "legacy_tokens.json")
    return token_mgr.TokenManager()


def test_invalid_token_logs_are_backfill_candidates(tmp_path):
    store = RequestLogStore(tmp_path / "request_logs.jsonl", max_items=100)
    store.add_payload(
        {
            "id": "poll-invalid",
            "ts": 100,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 401,
            "duration_sec": 12,
            "operation": "chat.completions",
            "error": "Token invalid or expired.",
            "task_status": "FAILED",
            "upstream_job_id": "job_123",
            "token_id": "tok_1",
            "token_account_email": "user@example.test",
        }
    )
    store.add_payload(
        {
            "id": "http-401-without-error-text",
            "ts": 101,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 401,
            "duration_sec": 1,
            "operation": "chat.completions",
            "error": None,
            "task_status": "FAILED",
            "token_id": "tok_2",
        }
    )
    store.add_payload(
        {
            "id": "server-error",
            "ts": 102,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 503,
            "duration_sec": 1,
            "operation": "chat.completions",
            "error": "upstream temporary error",
            "task_status": "FAILED",
            "token_id": "tok_3",
        }
    )

    result = store.find_poll_invalid_token_candidates()

    assert result["matched_logs"] == 1
    assert result["candidate_count"] == 1
    assert {item["token_id"] for item in result["candidates"]} == {"tok_1"}


def test_error_details_are_backfill_candidates(tmp_path):
    store = ErrorDetailStore(tmp_path / "request_errors.jsonl", max_items=100)
    store.add(
        ErrorDetailRecord(
            code="ERR-A",
            ts=100,
            message="Token invalid or expired.",
            status_code=401,
            operation="chat.completions",
            path="/v1/chat/completions",
            token_id="tok_1",
            token_account_email="user@example.test",
        )
    )
    store.add(
        ErrorDetailRecord(
            code="ERR-B",
            ts=101,
            message="Unauthorized",
            status_code=401,
            operation="chat.completions",
            path="/v1/chat/completions",
            token_id="tok_2",
        )
    )
    store.add(
        ErrorDetailRecord(
            code="ERR-C",
            ts=102,
            message="temporary upstream error",
            status_code=503,
            operation="chat.completions",
            path="/v1/chat/completions",
            token_id="tok_3",
        )
    )

    result = store.find_invalid_token_candidates()

    assert result["matched_logs"] == 1
    assert result["candidate_count"] == 1
    assert {item["token_id"] for item in result["candidates"]} == {"tok_1"}


def test_report_exhausted_by_identity_disables_active_pool(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    token = manager.add(
        "token-value",
        {
            "id": "tok_1",
            "source": "auto_refresh",
            "auto_refresh": True,
            "refresh_profile_id": "profile_1",
            "refresh_profile_email": "user@example.test",
        },
    )

    updated = manager.report_exhausted_by_identity(token_id=token["id"])

    assert updated["status"] == "exhausted"
    assert updated["_previous_status"] == "active"
    assert manager.get_available() is None


def test_report_invalid_by_identity_disables_active_pool(tmp_path, monkeypatch):
    manager = make_token_manager(tmp_path, monkeypatch)
    token = manager.add(
        "token-value",
        {
            "id": "tok_1",
            "source": "auto_refresh",
            "auto_refresh": True,
            "refresh_profile_id": "profile_1",
            "refresh_profile_email": "user@example.test",
        },
    )

    updated = manager.report_invalid_by_identity(token_id=token["id"])

    assert updated["status"] == "invalid"
    assert updated["_previous_status"] == "active"
    assert manager.get_available() is None
