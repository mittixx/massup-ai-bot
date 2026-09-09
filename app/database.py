from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # Reads must also wait while an owner-requested restore replaces the
        # database. RLock keeps existing nested write calls safe.
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    telegram_user_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    sex TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    height_cm REAL NOT NULL,
                    weight_kg REAL NOT NULL,
                    target_weight_kg REAL NOT NULL,
                    activity TEXT NOT NULL,
                    meals_per_day INTEGER NOT NULL,
                    allergies TEXT NOT NULL DEFAULT '',
                    dislikes TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    stores TEXT NOT NULL DEFAULT '',
                    weekly_budget INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    eaten_on TEXT NOT NULL,
                    meal_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    grams REAL NOT NULL DEFAULT 0,
                    kcal REAL NOT NULL,
                    protein REAL NOT NULL,
                    fat REAL NOT NULL,
                    carbs REAL NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL,
                    details_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES profiles(telegram_user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS weights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    measured_on TEXT NOT NULL,
                    weight_kg REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(telegram_user_id, measured_on),
                    FOREIGN KEY (telegram_user_id) REFERENCES profiles(telegram_user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    created_on TEXT NOT NULL,
                    budget INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES profiles(telegram_user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS reminder_settings (
                    telegram_user_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    meal_reminders INTEGER NOT NULL DEFAULT 1,
                    weigh_reminder INTEGER NOT NULL DEFAULT 1,
                    weekly_report INTEGER NOT NULL DEFAULT 1,
                    breakfast_time TEXT NOT NULL DEFAULT '09:00',
                    lunch_time TEXT NOT NULL DEFAULT '14:00',
                    evening_time TEXT NOT NULL DEFAULT '20:30',
                    weigh_weekday INTEGER NOT NULL DEFAULT 0,
                    weigh_time TEXT NOT NULL DEFAULT '09:00',
                    weekly_time TEXT NOT NULL DEFAULT '19:00',
                    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_user_id) REFERENCES profiles(telegram_user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS reminder_log (
                    telegram_user_id INTEGER NOT NULL,
                    reminder_kind TEXT NOT NULL,
                    sent_on TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_user_id, reminder_kind, sent_on),
                    FOREIGN KEY (telegram_user_id) REFERENCES profiles(telegram_user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS admin_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_meals_user_date ON meals(telegram_user_id, eaten_on);
                CREATE INDEX IF NOT EXISTS idx_weights_user_date ON weights(telegram_user_id, measured_on);
                CREATE INDEX IF NOT EXISTS idx_reminder_enabled ON reminder_settings(enabled);
                CREATE INDEX IF NOT EXISTS idx_admin_events_created ON admin_events(created_at);
                CREATE INDEX IF NOT EXISTS idx_admin_events_user ON admin_events(telegram_user_id);
                CREATE INDEX IF NOT EXISTS idx_admin_events_type_status ON admin_events(event_type, status);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def upsert_profile(self, data: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        fields = (
            "telegram_user_id", "name", "sex", "age", "height_cm", "weight_kg",
            "target_weight_kg", "activity", "meals_per_day", "allergies", "dislikes",
            "city", "stores", "weekly_budget",
        )
        values = [data[field] for field in fields]
        with self._lock, self.connect() as db:
            db.execute(
                f"""
                INSERT INTO profiles ({', '.join(fields)}, created_at, updated_at)
                VALUES ({', '.join('?' for _ in fields)}, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    name=excluded.name, sex=excluded.sex, age=excluded.age,
                    height_cm=excluded.height_cm, weight_kg=excluded.weight_kg,
                    target_weight_kg=excluded.target_weight_kg, activity=excluded.activity,
                    meals_per_day=excluded.meals_per_day, allergies=excluded.allergies,
                    dislikes=excluded.dislikes, city=excluded.city, stores=excluded.stores,
                    weekly_budget=excluded.weekly_budget, updated_at=excluded.updated_at
                """,
                [*values, now, now],
            )
        return self.get_profile(int(data["telegram_user_id"])) or data

    def get_profile(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM profiles WHERE telegram_user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def add_meal(self, data: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        with self._lock, self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO meals (
                    telegram_user_id, eaten_on, meal_type, name, grams, kcal,
                    protein, fat, carbs, source, confidence, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["telegram_user_id"], str(data.get("eaten_on", date.today())),
                    data.get("meal_type", "Приём пищи"), data["name"], data.get("grams", 0),
                    data["kcal"], data["protein"], data["fat"], data["carbs"],
                    data.get("source", "manual"), data.get("confidence"),
                    json.dumps(data.get("details"), ensure_ascii=False) if data.get("details") else None,
                    now,
                ),
            )
            meal_id = cursor.lastrowid
            row = db.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
        return dict(row)

    def update_meal(self, meal_id: int, user_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock, self.connect() as db:
            db.execute(
                """
                UPDATE meals SET name=?, grams=?, kcal=?, protein=?, fat=?, carbs=?
                WHERE id=? AND telegram_user_id=?
                """,
                (data["name"], data["grams"], data["kcal"], data["protein"],
                 data["fat"], data["carbs"], meal_id, user_id),
            )
            row = db.execute(
                "SELECT * FROM meals WHERE id=? AND telegram_user_id=?", (meal_id, user_id)
            ).fetchone()
        return dict(row) if row else None

    def delete_meal(self, meal_id: int, user_id: int) -> bool:
        with self._lock, self.connect() as db:
            cursor = db.execute(
                "DELETE FROM meals WHERE id=? AND telegram_user_id=?", (meal_id, user_id)
            )
        return cursor.rowcount > 0

    def meals_for_day(self, user_id: int, eaten_on: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM meals WHERE telegram_user_id=? AND eaten_on=? ORDER BY id DESC",
                (user_id, eaten_on),
            ).fetchall()
        return [dict(row) for row in rows]

    def meals_between(self, user_id: int, start: str, end: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM meals
                WHERE telegram_user_id=? AND eaten_on BETWEEN ? AND ?
                ORDER BY eaten_on, id
                """,
                (user_id, start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_weight(self, user_id: int, measured_on: str, weight_kg: float) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                """
                INSERT INTO weights(telegram_user_id, measured_on, weight_kg, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_user_id, measured_on)
                DO UPDATE SET weight_kg=excluded.weight_kg
                """,
                (user_id, measured_on, weight_kg, self._now()),
            )
            db.execute(
                """UPDATE profiles SET weight_kg=(
                    SELECT weight_kg FROM weights WHERE telegram_user_id=?
                    ORDER BY measured_on DESC LIMIT 1
                ), updated_at=? WHERE telegram_user_id=?""",
                (user_id, self._now(), user_id),
            )

    def weights(self, user_id: int, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT measured_on, weight_kg FROM weights
                WHERE telegram_user_id=? ORDER BY measured_on DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def backup_to(self, destination: str) -> str:
        """Create a consistent SQLite snapshot while the bot stays online."""
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            source = sqlite3.connect(self.path, timeout=30)
            backup = sqlite3.connect(destination, timeout=30)
            try:
                source.backup(backup)
            finally:
                backup.close()
                source.close()
        return destination

    @staticmethod
    def _validate_restore_source(connection: sqlite3.Connection) -> None:
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("Повреждённый файл SQLite.")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {"profiles", "meals", "weights", "plans"}
        if not required.issubset(tables):
            raise ValueError("Это не резервная копия MassUp AI.")

    def restore_from(self, source_path: str) -> None:
        """Validate and restore a MassUp SQLite backup without losing the live DB on failure."""
        source_file = Path(source_path)
        if not source_file.is_file() or source_file.stat().st_size == 0:
            raise ValueError("Файл резервной копии пуст или не найден.")

        with sqlite3.connect(source_file, timeout=30) as source:
            self._validate_restore_source(source)

        rollback_path = Path(self.path).with_suffix(".before-restore.db")
        with self._lock:
            current = sqlite3.connect(self.path, timeout=30)
            rollback = sqlite3.connect(rollback_path, timeout=30)
            source = sqlite3.connect(source_file, timeout=30)
            try:
                current.backup(rollback)
                source.backup(current)
                self._validate_restore_source(current)
            except Exception:
                rollback.backup(current)
                raise
            finally:
                source.close()
                rollback.close()
                current.close()
                rollback_path.unlink(missing_ok=True)

        # Add any schema/index additions from the running release.
        self.initialize()

    def save_plan(self, user_id: int, budget: int, plan: dict[str, Any]) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                """
                INSERT INTO plans(telegram_user_id, created_on, budget, plan_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, str(date.today()), budget, json.dumps(plan, ensure_ascii=False),
                 self._now()),
            )

    def latest_plan(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT plan_json FROM plans WHERE telegram_user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return json.loads(row["plan_json"]) if row else None

    @staticmethod
    def default_reminders(user_id: int) -> dict[str, Any]:
        return {
            "telegram_user_id": user_id,
            "enabled": False,
            "meal_reminders": True,
            "weigh_reminder": True,
            "weekly_report": True,
            "breakfast_time": "09:00",
            "lunch_time": "14:00",
            "evening_time": "20:30",
            "weigh_weekday": 0,
            "weigh_time": "09:00",
            "weekly_time": "19:00",
            "timezone": "Europe/Moscow",
        }

    def get_reminders(self, user_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM reminder_settings WHERE telegram_user_id=?", (user_id,)
            ).fetchone()
        if not row:
            return self.default_reminders(user_id)
        result = dict(row)
        for field in ("enabled", "meal_reminders", "weigh_reminder", "weekly_report"):
            result[field] = bool(result[field])
        result.pop("updated_at", None)
        return result

    def save_reminders(self, data: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "telegram_user_id", "enabled", "meal_reminders", "weigh_reminder",
            "weekly_report", "breakfast_time", "lunch_time", "evening_time",
            "weigh_weekday", "weigh_time", "weekly_time", "timezone",
        )
        values = [int(data[field]) if isinstance(data[field], bool) else data[field] for field in fields]
        with self._lock, self.connect() as db:
            db.execute(
                f"""
                INSERT INTO reminder_settings ({', '.join(fields)}, updated_at)
                VALUES ({', '.join('?' for _ in fields)}, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    meal_reminders=excluded.meal_reminders,
                    weigh_reminder=excluded.weigh_reminder,
                    weekly_report=excluded.weekly_report,
                    breakfast_time=excluded.breakfast_time,
                    lunch_time=excluded.lunch_time,
                    evening_time=excluded.evening_time,
                    weigh_weekday=excluded.weigh_weekday,
                    weigh_time=excluded.weigh_time,
                    weekly_time=excluded.weekly_time,
                    timezone=excluded.timezone,
                    updated_at=excluded.updated_at
                """,
                [*values, self._now()],
            )
        return self.get_reminders(int(data["telegram_user_id"]))

    def enabled_reminders(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM reminder_settings WHERE enabled=1"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for field in ("enabled", "meal_reminders", "weigh_reminder", "weekly_report"):
                item[field] = bool(item[field])
            result.append(item)
        return result

    def claim_reminder(self, user_id: int, kind: str, sent_on: str) -> bool:
        with self._lock, self.connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO reminder_log(
                    telegram_user_id, reminder_kind, sent_on, sent_at
                ) VALUES (?, ?, ?, ?)
                """,
                (user_id, kind, sent_on, self._now()),
            )
        return cursor.rowcount > 0

    def record_event(
        self,
        user_id: int | None,
        event_type: str,
        status: str = "ok",
        detail: str = "",
    ) -> None:
        """Store a short, secret-free operational event for the owner dashboard."""
        safe_type = event_type.strip()[:60] or "unknown"
        safe_status = status if status in {"ok", "error"} else "error"
        safe_detail = detail.strip()[:160]
        now = self._now()
        cutoff = (datetime.now(UTC) - timedelta(days=180)).isoformat()
        with self._lock, self.connect() as db:
            db.execute(
                """
                INSERT INTO admin_events(
                    telegram_user_id, event_type, status, detail, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, safe_type, safe_status, safe_detail, now),
            )
            db.execute("DELETE FROM admin_events WHERE created_at < ?", (cutoff,))

    def admin_overview(self) -> dict[str, Any]:
        """Aggregate owner-facing metrics without returning private profile fields."""
        now = datetime.now(UTC)
        today = now.date()
        today_text = str(today)
        week_cutoff = (now - timedelta(days=7)).isoformat()
        today_cutoff = datetime.combine(today, datetime.min.time(), tzinfo=UTC).isoformat()
        with self.connect() as db:
            def scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
                row = db.execute(sql, params).fetchone()
                return int(row[0] or 0) if row else 0

            total_users = scalar(
                """
                SELECT COUNT(*) FROM (
                    SELECT telegram_user_id FROM profiles
                    UNION
                    SELECT telegram_user_id FROM admin_events
                    WHERE telegram_user_id IS NOT NULL
                )
                """
            )
            activity_sql = """
                SELECT COUNT(*) FROM (
                    SELECT telegram_user_id FROM admin_events WHERE created_at >= ?
                    UNION SELECT telegram_user_id FROM meals WHERE created_at >= ?
                    UNION SELECT telegram_user_id FROM weights WHERE created_at >= ?
                    UNION SELECT telegram_user_id FROM plans WHERE created_at >= ?
                    UNION SELECT telegram_user_id FROM profiles WHERE updated_at >= ?
                )
            """
            active_today = scalar(activity_sql, (today_cutoff,) * 5)
            active_7d = scalar(activity_sql, (week_cutoff,) * 5)
            metrics = {
                "total_users": total_users,
                "profiles": scalar("SELECT COUNT(*) FROM profiles"),
                "active_today": active_today,
                "active_7d": active_7d,
                "meals_today": scalar(
                    "SELECT COUNT(*) FROM meals WHERE eaten_on=?", (today_text,)
                ),
                "meals_total": scalar("SELECT COUNT(*) FROM meals"),
                "plans_total": scalar("SELECT COUNT(*) FROM plans"),
                "weights_total": scalar("SELECT COUNT(*) FROM weights"),
                "reminders_enabled": scalar(
                    "SELECT COUNT(*) FROM reminder_settings WHERE enabled=1"
                ),
                "ai_requests_7d": scalar(
                    "SELECT COUNT(*) FROM admin_events WHERE event_type LIKE 'ai_%' AND created_at>=?",
                    (week_cutoff,),
                ),
                "ai_errors_7d": scalar(
                    """SELECT COUNT(*) FROM admin_events
                       WHERE event_type LIKE 'ai_%' AND status='error' AND created_at>=?""",
                    (week_cutoff,),
                ),
            }

            activity_rows = db.execute(
                """
                WITH activity AS (
                    SELECT telegram_user_id, substr(created_at, 1, 10) AS day FROM admin_events
                    UNION ALL SELECT telegram_user_id, substr(created_at, 1, 10) FROM meals
                    UNION ALL SELECT telegram_user_id, substr(created_at, 1, 10) FROM weights
                    UNION ALL SELECT telegram_user_id, substr(created_at, 1, 10) FROM plans
                    UNION ALL SELECT telegram_user_id, substr(updated_at, 1, 10) FROM profiles
                )
                SELECT day, COUNT(DISTINCT telegram_user_id) AS users
                FROM activity WHERE day >= ? GROUP BY day
                """,
                (str(today - timedelta(days=13)),),
            ).fetchall()
            ai_rows = db.execute(
                """
                SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS requests
                FROM admin_events
                WHERE event_type LIKE 'ai_%' AND created_at >= ?
                GROUP BY day
                """,
                ((now - timedelta(days=14)).isoformat(),),
            ).fetchall()
            activity_by_day = {row["day"]: int(row["users"]) for row in activity_rows}
            ai_by_day = {row["day"]: int(row["requests"]) for row in ai_rows}
            trend = []
            for offset in range(13, -1, -1):
                day = str(today - timedelta(days=offset))
                trend.append({
                    "day": day,
                    "active_users": activity_by_day.get(day, 0),
                    "ai_requests": ai_by_day.get(day, 0),
                })

            recent_rows = db.execute(
                """
                SELECT event_type, status, detail, telegram_user_id, created_at
                FROM admin_events ORDER BY id DESC LIMIT 30
                """
            ).fetchall()

        database_size = Path(self.path).stat().st_size if Path(self.path).exists() else 0
        return {
            "metrics": metrics,
            "trend": trend,
            "recent_events": [dict(row) for row in recent_rows],
            "database_size_bytes": database_size,
            "generated_at": now.isoformat(),
        }

    def admin_users(self, search: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Return a compact user activity list for the owner dashboard."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        search_value = f"%{search.strip()}%"
        cte = """
            WITH ids AS (
                SELECT telegram_user_id FROM profiles
                UNION
                SELECT telegram_user_id FROM admin_events WHERE telegram_user_id IS NOT NULL
            ), activity AS (
                SELECT telegram_user_id, created_at FROM admin_events
                UNION ALL SELECT telegram_user_id, created_at FROM meals
                UNION ALL SELECT telegram_user_id, created_at FROM weights
                UNION ALL SELECT telegram_user_id, created_at FROM plans
                UNION ALL SELECT telegram_user_id, updated_at FROM profiles
            ), last_seen AS (
                SELECT telegram_user_id, MAX(created_at) AS last_activity
                FROM activity GROUP BY telegram_user_id
            )
        """
        where = """
            WHERE (? = '%%' OR COALESCE(p.name, '') LIKE ?
                   OR CAST(ids.telegram_user_id AS TEXT) LIKE ?)
        """
        with self.connect() as db:
            count_row = db.execute(
                cte + """
                SELECT COUNT(*) FROM ids
                LEFT JOIN profiles p ON p.telegram_user_id=ids.telegram_user_id
                """ + where,
                (search_value, search_value, search_value),
            ).fetchone()
            rows = db.execute(
                cte + """
                SELECT
                    ids.telegram_user_id,
                    COALESCE(p.name, 'Без профиля') AS name,
                    COALESCE(p.city, '') AS city,
                    p.weight_kg,
                    p.target_weight_kg,
                    p.created_at AS joined_at,
                    last_seen.last_activity,
                    (SELECT COUNT(*) FROM meals m WHERE m.telegram_user_id=ids.telegram_user_id) AS meals_count,
                    (SELECT COUNT(*) FROM plans pl WHERE pl.telegram_user_id=ids.telegram_user_id) AS plans_count,
                    (SELECT COUNT(*) FROM admin_events e WHERE e.telegram_user_id=ids.telegram_user_id
                        AND e.event_type LIKE 'ai_%') AS ai_requests,
                    EXISTS(SELECT 1 FROM reminder_settings r
                        WHERE r.telegram_user_id=ids.telegram_user_id AND r.enabled=1) AS reminders_enabled
                FROM ids
                LEFT JOIN profiles p ON p.telegram_user_id=ids.telegram_user_id
                LEFT JOIN last_seen ON last_seen.telegram_user_id=ids.telegram_user_id
                """ + where + """
                ORDER BY last_seen.last_activity DESC, ids.telegram_user_id DESC
                LIMIT ? OFFSET ?
                """,
                (search_value, search_value, search_value, limit, offset),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["reminders_enabled"] = bool(item["reminders_enabled"])
        return {"items": items, "total": int(count_row[0] if count_row else 0)}
