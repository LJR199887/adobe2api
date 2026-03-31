import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import socket

import requests
import uvicorn

import app as app_module


class RequestProgressApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request_id = "req-progress-001"
        self.api_key = "test-service-key"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "request_logs.jsonl"
        self.log_path.write_text("", encoding="utf-8")
        self.original_log_path = app_module.log_store._file_path
        self.original_append_since_truncate = app_module.log_store._append_since_truncate
        app_module.log_store._file_path = self.log_path
        app_module.log_store._append_since_truncate = 0
        with app_module.live_log_store._lock:
            app_module.live_log_store._items.clear()
        self.generated_before = {
            item.name for item in Path(app_module.GENERATED_DIR).glob("*")
        }
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                app_module.app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
            )
        )
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()
        self._wait_for_server()

    def tearDown(self) -> None:
        self.server.should_exit = True
        self.server_thread.join(timeout=5)
        app_module.log_store._file_path = self.original_log_path
        app_module.log_store._append_since_truncate = (
            self.original_append_since_truncate
        )
        with app_module.live_log_store._lock:
            app_module.live_log_store._items.clear()
        for item in Path(app_module.GENERATED_DIR).glob("*"):
            if item.name not in self.generated_before and item.is_file():
                item.unlink(missing_ok=True)
        self.temp_dir.cleanup()

    def _wait_for_server(self) -> None:
        last_error = None
        for _ in range(50):
            try:
                response = requests.get(
                    f"{self.base_url}/api/v1/health",
                    timeout=0.5,
                )
                if response.status_code == 200:
                    return
            except requests.RequestException as exc:
                last_error = exc
            threading.Event().wait(0.1)
        raise RuntimeError(f"server did not start in time: {last_error}")

    def test_public_request_progress_polling(self) -> None:
        progress_started = threading.Event()
        allow_finish = threading.Event()
        response_holder: dict[str, object] = {}

        def fake_config_get(key: str, default=None):
            if key == "api_key":
                return self.api_key
            if key == "public_base_url":
                return ""
            return default

        def fake_generate(**kwargs):
            progress_cb = kwargs.get("progress_cb")
            if callable(progress_cb):
                progress_cb(
                    {
                        "task_status": "IN_PROGRESS",
                        "task_progress": 42.0,
                        "upstream_job_id": "up-job-123",
                    }
                )
            progress_started.set()
            if not allow_finish.wait(timeout=5):
                raise RuntimeError("test timed out waiting to finish generation")
            if callable(progress_cb):
                progress_cb(
                    {
                        "task_status": "COMPLETED",
                        "task_progress": 100.0,
                        "upstream_job_id": "up-job-123",
                    }
                )
            return b"fake-image-bytes", {"progress": 100.0}

        def send_request():
            response_holder["response"] = requests.post(
                f"{self.base_url}/v1/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "nano-banana-pro",
                    "prompt": "a cinematic mountain sunrise",
                    "request_id": self.request_id,
                },
                timeout=10,
            )

        with patch.object(app_module.config_manager, "get", side_effect=fake_config_get), patch.object(
            app_module.token_manager,
            "get_available",
            return_value="token-123",
        ), patch.object(
            app_module.token_manager,
            "get_meta_by_value",
            return_value={
                "token_id": "token-123",
                "token_account_name": "Test User",
                "token_account_email": "test@example.com",
                "token_source": "unit-test",
            },
        ), patch.object(
            app_module.token_manager,
            "report_exhausted",
            return_value=None,
        ), patch.object(
            app_module.token_manager,
            "report_invalid",
            return_value=None,
        ), patch.object(
            app_module.client,
            "generate",
            side_effect=fake_generate,
        ):
            worker = threading.Thread(target=send_request, daemon=True)
            worker.start()

            self.assertTrue(progress_started.wait(timeout=5))

            running = requests.get(
                f"{self.base_url}/v1/requests/{self.request_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            self.assertEqual(running.status_code, 200, running.text)
            running_data = running.json()
            self.assertEqual(running_data["request_id"], self.request_id)
            self.assertEqual(running_data["task_status"], "IN_PROGRESS")
            self.assertEqual(running_data["task_progress"], 42.0)
            self.assertEqual(running_data["upstream_job_id"], "up-job-123")
            self.assertEqual(running_data["source"], "live")
            self.assertFalse(running_data["done"])

            allow_finish.set()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())

            response = response_holder.get("response")
            self.assertIsNotNone(response)
            response = response_holder["response"]
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers.get("X-Request-Id"), self.request_id)
            payload = response.json()
            self.assertEqual(payload["request_id"], self.request_id)
            self.assertIn("data", payload)

            finished = requests.get(
                f"{self.base_url}/v1/requests/{self.request_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            self.assertEqual(finished.status_code, 200, finished.text)
            finished_data = finished.json()
            self.assertEqual(finished_data["request_id"], self.request_id)
            self.assertEqual(finished_data["task_status"], "COMPLETED")
            self.assertEqual(finished_data["task_progress"], 100.0)
            self.assertEqual(finished_data["upstream_job_id"], "up-job-123")
            self.assertEqual(finished_data["source"], "log")
            self.assertTrue(finished_data["done"])
            self.assertTrue(str(finished_data.get("preview_url") or "").endswith(".png"))


if __name__ == "__main__":
    unittest.main()
