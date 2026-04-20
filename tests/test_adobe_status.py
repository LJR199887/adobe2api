from core.adobe_client import AdobeClient


class DummyResponse:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_extract_task_status_reads_nested_failed_status():
    client = AdobeClient()
    status = client._extract_task_status(
        {"task": {"status": "failed"}, "outputs": []},
        DummyResponse(),
    )

    assert status == "FAILED"
    assert client._is_failed_status(status)


def test_extract_task_status_treats_error_payload_as_failed():
    client = AdobeClient()
    status = client._extract_task_status(
        {"result": {"error": {"message": "content policy rejection"}}},
        DummyResponse(),
    )

    assert status == "FAILED"


def test_extract_task_status_error_overrides_in_progress():
    client = AdobeClient()
    status = client._extract_task_status(
        {"status": "IN_PROGRESS", "error": {"message": "content policy rejection"}},
        DummyResponse(),
    )

    assert status == "FAILED"


def test_extract_task_status_uses_status_header_aliases():
    client = AdobeClient()
    status = client._extract_task_status(
        {},
        DummyResponse(headers={"x-task-status": "canceled"}),
    )

    assert status == "CANCELLED"
