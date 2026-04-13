import time
from typing import Any

import requests


def resolve_basic_proxy(cfg: dict) -> str:
    proxy = str(cfg.get("proxy", "") or "").strip()
    use_proxy = bool(cfg.get("use_proxy", False))
    return proxy if use_proxy and proxy else ""


def resolve_resource_proxy(cfg: dict) -> str:
    proxy = str(cfg.get("resource_proxy", "") or "").strip()
    use_proxy = bool(cfg.get("resource_use_proxy", False))
    return proxy if use_proxy and proxy else ""


def build_requests_proxies(proxy: str) -> dict[str, str] | None:
    raw = str(proxy or "").strip()
    if not raw:
        return None
    return {"http": raw, "https": raw}


def test_proxy_endpoint(
    *,
    proxy_label: str,
    proxy: str,
    target_url: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    started = time.time()
    if not proxy:
        return {
            "name": proxy_label,
            "enabled": False,
            "ok": False,
            "target_url": target_url,
            "proxy": "",
            "elapsed_ms": 0,
            "status_code": None,
            "message": "proxy disabled",
        }

    try:
        resp = requests.get(
            target_url,
            timeout=max(1.0, float(timeout)),
            proxies=build_requests_proxies(proxy),
            allow_redirects=False,
        )
        elapsed_ms = round((time.time() - started) * 1000, 2)
        return {
            "name": proxy_label,
            "enabled": True,
            "ok": True,
            "target_url": target_url,
            "proxy": proxy,
            "elapsed_ms": elapsed_ms,
            "status_code": int(resp.status_code),
            "message": f"http response received ({resp.status_code})",
        }
    except Exception as exc:
        elapsed_ms = round((time.time() - started) * 1000, 2)
        return {
            "name": proxy_label,
            "enabled": True,
            "ok": False,
            "target_url": target_url,
            "proxy": proxy,
            "elapsed_ms": elapsed_ms,
            "status_code": None,
            "message": str(exc),
        }
