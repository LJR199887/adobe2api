import json
import base64
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
                        credits_error TEXT,
                        expires_at REAL,
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
                token_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(tokens)").fetchall()
                }
                if "credits_error" not in token_columns:
                    conn.execute("ALTER TABLE tokens ADD COLUMN credits_error TEXT")
                if "expires_at" not in token_columns:
                    conn.execute("ALTER TABLE tokens ADD COLUMN expires_at REAL")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tokens_credits_error "
                    "ON tokens(credits_error)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tokens_status_expires_at "
                    "ON tokens(status, expires_at)"
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
    def _decode_token_expires_at(token_value: str):
        value = SQLiteStore._token_value_key({"value": token_value})
        parts = value.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        try:
            payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
        except Exception:
            return None

        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)

        created_at = payload.get("created_at")
        expires_in = payload.get("expires_in")
        try:
            created_at_val = int(str(created_at).strip())
            expires_in_val = int(str(expires_in).strip())
        except Exception:
            return None

        if created_at_val <= 0 or expires_in_val <= 0:
            return None
        if created_at_val > 10_000_000_000:
            created_at_val = int(created_at_val / 1000)
        if expires_in_val > 86400 * 2:
            expires_in_val = int(expires_in_val / 1000)
        return created_at_val + expires_in_val

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
            str(token.get("credits_error") or "").strip(),
            cls._decode_token_expires_at(token.get("value") or ""),
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
                    id, value_key, profile_id, status, email, name, credits_error,
                    expires_at, added_at, updated_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def update_tokens(self, tokens: Iterable[Dict]):
        self._ensure_schema()
        rows = [self._token_row(token) for token in tokens if isinstance(token, dict)]
        if not rows:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO tokens (
                    id, value_key, profile_id, status, email, name, credits_error,
                    expires_at, added_at, updated_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def list_earliest_expiring_active_tokens(self, limit: int = 200) -> List[Dict]:
        self._ensure_schema()
        limit = max(1, min(1000, int(limit or 200)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM tokens
                WHERE status = 'active'
                  AND expires_at IS NOT NULL
                  AND expires_at > 0
                ORDER BY expires_at ASC, updated_at ASC, added_at ASC, id ASC
                LIMIT ?
                """,
                [limit],
            ).fetchall()

        tokens: List[Dict] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            if isinstance(payload, dict):
                tokens.append(payload)
        return tokens

    @staticmethod
    def _token_filter_sql(status: str = "", credits: str = ""):
        clauses = []
        params = []
        status_filter = str(status or "").strip().lower()
        credits_filter = str(credits or "").strip().lower()
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        if credits_filter == "error":
            clauses.append("COALESCE(credits_error, '') <> ''")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def list_tokens_page(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status: str = "",
        credits: str = "",
    ) -> Dict:
        self._ensure_schema()
        page = max(1, int(page or 1))
        page_size = max(1, min(200, int(page_size or 50)))
        where_sql, params = self._token_filter_sql(status=status, credits=credits)
        sort_sql = """
            CASE
                WHEN updated_at IS NOT NULL AND updated_at > 0 THEN updated_at
                WHEN added_at IS NOT NULL AND added_at > 0 THEN added_at
                ELSE 0
            END DESC,
            COALESCE(added_at, 0) DESC,
            id DESC
        """
        with self._lock, self._connect() as conn:
            total_count = int(
                conn.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] or 0
            )
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tokens "
                    "WHERE status = 'active'"
                ).fetchone()[0]
                or 0
            )
            filtered_count = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM tokens {where_sql}",
                    params,
                ).fetchone()[0]
                or 0
            )
            total_pages = max(1, (filtered_count + page_size - 1) // page_size)
            page = min(page, total_pages)
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT payload
                FROM tokens
                {where_sql}
                ORDER BY {sort_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()

        tokens: List[Dict] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                continue
            if isinstance(payload, dict):
                tokens.append(payload)
        return {
            "tokens": tokens,
            "summary": {
                "total": total_count,
                "active": active_count,
                "filtered": filtered_count,
            },
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": filtered_count,
                "total_pages": total_pages,
            },
        }

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
