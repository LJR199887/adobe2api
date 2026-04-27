import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List


class SQLiteStore:
    """Small persistence layer shared by token and refresh-profile managers."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tokens (
                        id TEXT PRIMARY KEY,
                        value_key TEXT,
                        profile_id TEXT,
                        status TEXT,
                        email TEXT,
                        name TEXT,
                        added_at REAL,
                        updated_at REAL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS refresh_profiles (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT,
                        email TEXT,
                        name TEXT,
                        enabled INTEGER,
                        imported_at REAL,
                        updated_at REAL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tokens_value_key ON tokens(value_key)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tokens_profile_id ON tokens(profile_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tokens_status ON tokens(status)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_refresh_profiles_fingerprint "
                    "ON refresh_profiles(fingerprint)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_refresh_profiles_enabled "
                    "ON refresh_profiles(enabled)"
                )
                conn.execute("PRAGMA user_version=1")
            self._initialized = True

    @staticmethod
    def _json_payload(item: Dict) -> str:
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _token_value_key(token: Dict) -> str:
        value = str(token.get("value") or "").strip()
        if value.startswith("Bearer "):
            value = value[7:].strip()
        return value

    @staticmethod
    def _number(value, default=0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @classmethod
    def _token_row(cls, token: Dict):
        now_ts = time.time()
        token_id = str(token.get("id") or "").strip()
        return (
            token_id,
            cls._token_value_key(token),
            str(token.get("refresh_profile_id") or "").strip(),
            str(token.get("status") or "active").strip(),
            str(token.get("refresh_profile_email") or "").strip(),
            str(token.get("refresh_profile_name") or "").strip(),
            cls._number(token.get("added_at"), now_ts),
            cls._number(token.get("updated_at"), token.get("added_at") or now_ts),
            cls._json_payload(token),
        )

    @staticmethod
    def _profile_cookie_fingerprint(profile: Dict) -> str:
        endpoint = profile.get("endpoint") if isinstance(profile, dict) else {}
        if not isinstance(endpoint, dict):
            return ""
        headers = (
            endpoint.get("headers") if isinstance(endpoint.get("headers"), dict) else {}
        )
        cookie = str(headers.get("Cookie") or "").strip()
        if not cookie:
            return ""

        pairs = []
        for part in cookie.split(";"):
            text = part.strip()
            if not text:
                continue
            if "=" in text:
                key, val = text.split("=", 1)
                key = key.strip()
                val = val.strip()
            else:
                key = text.strip()
                val = ""
            if key:
                pairs.append([key, val])
        if not pairs:
            return ""
        pairs.sort(key=lambda item: (item[0].casefold(), item[0], item[1]))
        return json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _profile_row(cls, profile: Dict):
        now_ts = time.time()
        account = (
            profile.get("account") if isinstance(profile.get("account"), dict) else {}
        )
        state = profile.get("state") if isinstance(profile.get("state"), dict) else {}
        profile_id = str(profile.get("id") or "").strip()
        updated_at = (
            account.get("updated_at")
            or state.get("last_success_at")
            or profile.get("imported_at")
        )
        return (
            profile_id,
            cls._profile_cookie_fingerprint(profile),
            str(account.get("email") or "").strip(),
            str(profile.get("name") or "").strip(),
            1 if bool(profile.get("enabled", True)) else 0,
            cls._number(profile.get("imported_at"), now_ts),
            cls._number(updated_at, now_ts),
            cls._json_payload(profile),
        )

    def load_tokens(self) -> List[Dict]:
        self._ensure_schema()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM tokens ORDER BY added_at ASC, id ASC"
            ).fetchall()
        items: List[Dict] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(payload)
        return items

    def replace_tokens(self, tokens: Iterable[Dict]):
        self._ensure_schema()
        rows = [self._token_row(token) for token in tokens if isinstance(token, dict)]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM tokens")
            conn.executemany(
                """
                INSERT OR REPLACE INTO tokens (
                    id, value_key, profile_id, status, email, name,
                    added_at, updated_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def load_refresh_profiles(self) -> List[Dict]:
        self._ensure_schema()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM refresh_profiles ORDER BY imported_at ASC, id ASC"
            ).fetchall()
        items: List[Dict] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            if isinstance(payload, dict):
                items.append(payload)
        return items

    def replace_refresh_profiles(self, profiles: Iterable[Dict]):
        self._ensure_schema()
        rows = [
            self._profile_row(profile)
            for profile in profiles
            if isinstance(profile, dict)
        ]
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM refresh_profiles")
            conn.executemany(
                """
                INSERT OR REPLACE INTO refresh_profiles (
                    id, fingerprint, email, name, enabled,
                    imported_at, updated_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
