import time

from core.stores import RequestLogStore


def make_store(tmp_path):
    return RequestLogStore(tmp_path / "request_logs.jsonl", max_items=100)


def test_failed_only_includes_failed_task_status_with_http_200(tmp_path):
    store = make_store(tmp_path)
    now = time.time()
    store.add_payload(
        {
            "id": "failed-task",
            "ts": now,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 200,
            "duration_sec": 0,
            "operation": "chat.completions",
            "task_status": "FAILED",
            "preview_url": None,
            "token_account_email": "failed@example.com",
        }
    )

    logs, total = store.list(failed_only=True)
    accounts = store.list_failed_accounts()

    assert total == 1
    assert logs[0]["id"] == "failed-task"
    assert accounts[0]["account_key"] == "failed@example.com"


def test_successful_generation_without_preview_counts_as_failed(tmp_path):
    store = make_store(tmp_path)
    now = time.time()
    store.add_payload(
        {
            "id": "empty-success",
            "ts": now,
            "method": "POST",
            "path": "/v1/chat/completions",
            "status_code": 200,
            "duration_sec": 0,
            "operation": "chat.completions",
            "task_status": "COMPLETED",
            "preview_url": None,
            "preview_kind": "video",
        }
    )
    store.add_payload(
        {
            "id": "real-success",
            "ts": now,
            "method": "POST",
            "path": "/v1/images/generations",
            "status_code": 200,
            "duration_sec": 10,
            "operation": "images.generations",
            "task_status": "COMPLETED",
            "preview_url": "http://127.0.0.1/generated/out.png",
            "preview_kind": "image",
        }
    )

    failed_logs, failed_total = store.list(failed_only=True)
    stats = store.stats(start_ts=now - 1, end_ts=now + 1)

    assert failed_total == 1
    assert failed_logs[0]["id"] == "empty-success"
    assert stats["failed_requests"] == 1
    assert stats["generated_images"] == 1
    assert stats["generated_videos"] == 0
