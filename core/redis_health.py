import os
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse


@dataclass
class RedisConfig:
    host: str
    port: int
    password: str = ""
    username: str = ""
    db: int = 0
    timeout: float = 5.0
    use_ssl: bool = False


def _env_first(*keys: str) -> str:
    for key in keys:
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    return ""


def load_redis_config_from_env() -> Optional[RedisConfig]:
    url = _env_first("REDIS_URL", "REDIS_URI")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            return None
        db = 0
        try:
            raw_path = str(parsed.path or "").strip("/")
            if raw_path:
                db = max(0, int(raw_path))
        except Exception:
            db = 0
        return RedisConfig(
            host=str(parsed.hostname),
            port=int(parsed.port or 6379),
            username=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            db=db,
            timeout=_load_timeout(),
            use_ssl=(parsed.scheme == "rediss"),
        )

    host = _env_first("REDIS_HOST")
    if not host:
        return None
    try:
        port = int(_env_first("REDIS_PORT") or "6379")
    except Exception:
        port = 6379
    try:
        db = int(_env_first("REDIS_DB") or "0")
    except Exception:
        db = 0
    ssl_raw = _env_first("REDIS_SSL", "REDIS_USE_SSL").lower()
    use_ssl = ssl_raw in {"1", "true", "yes", "on"}
    return RedisConfig(
        host=host,
        port=port,
        username=_env_first("REDIS_USERNAME"),
        password=_env_first("REDIS_PASSWORD"),
        db=max(0, db),
        timeout=_load_timeout(),
        use_ssl=use_ssl,
    )


def _load_timeout() -> float:
    raw = _env_first("REDIS_TIMEOUT", "REDIS_CONNECT_TIMEOUT")
    if not raw:
        return 5.0
    try:
        timeout = float(raw)
    except Exception:
        timeout = 5.0
    return min(max(timeout, 1.0), 30.0)


def _resp_command(*parts: str) -> bytes:
    encoded = [str(part).encode("utf-8") for part in parts]
    payload = [f"*{len(encoded)}\r\n".encode("ascii")]
    for item in encoded:
        payload.append(f"${len(item)}\r\n".encode("ascii"))
        payload.append(item + b"\r\n")
    return b"".join(payload)


def _read_resp_line(reader) -> str:
    raw = reader.readline()
    if not raw:
        raise RuntimeError("redis connection closed unexpectedly")
    if raw.startswith(b"+"):
        return raw[1:].decode("utf-8", errors="replace").strip()
    if raw.startswith(b"-"):
        raise RuntimeError(raw[1:].decode("utf-8", errors="replace").strip())
    if raw.startswith(b":"):
        return raw[1:].decode("utf-8", errors="replace").strip()
    raise RuntimeError(f"unexpected redis response: {raw[:80]!r}")


def check_redis_connection() -> dict:
    cfg = load_redis_config_from_env()
    if cfg is None:
        return {
            "configured": False,
            "ok": False,
            "host": None,
            "port": None,
            "db": None,
            "ssl": False,
            "checked_at": int(time.time()),
            "error": "redis environment variables not configured",
        }

    sock = None
    reader = None
    try:
        sock = socket.create_connection((cfg.host, cfg.port), timeout=cfg.timeout)
        sock.settimeout(cfg.timeout)
        if cfg.use_ssl:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=cfg.host)
        reader = sock.makefile("rb")

        if cfg.password:
            if cfg.username:
                sock.sendall(_resp_command("AUTH", cfg.username, cfg.password))
            else:
                sock.sendall(_resp_command("AUTH", cfg.password))
            _read_resp_line(reader)

        if int(cfg.db) > 0:
            sock.sendall(_resp_command("SELECT", str(cfg.db)))
            _read_resp_line(reader)

        sock.sendall(_resp_command("PING"))
        pong = _read_resp_line(reader).upper()
        if pong != "PONG":
            raise RuntimeError(f"unexpected ping response: {pong}")

        return {
            "configured": True,
            "ok": True,
            "host": cfg.host,
            "port": cfg.port,
            "db": cfg.db,
            "ssl": bool(cfg.use_ssl),
            "checked_at": int(time.time()),
            "error": None,
        }
    except Exception as exc:
        return {
            "configured": True,
            "ok": False,
            "host": cfg.host,
            "port": cfg.port,
            "db": cfg.db,
            "ssl": bool(cfg.use_ssl),
            "checked_at": int(time.time()),
            "error": str(exc),
        }
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
