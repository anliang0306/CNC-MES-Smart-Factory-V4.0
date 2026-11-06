"""SQLite durable outbox for telemetry batches awaiting MES API acknowledgement.

Delivery guarantees:
- messages keep their original order (in-order delivery, no reordering)
- a message is deleted only after the MES API acknowledged it
- permanently failing messages (4xx from the API, or too many retries) are moved
  to the dead letter state so one bad message can never block the whole queue
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

class Outbox:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS edge_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    dead INTEGER NOT NULL DEFAULT 0,
                    dead_reason TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS ix_edge_outbox_created ON edge_outbox(created_at, id)")
            # Old databases created before the dead-letter columns exist.
            for column, ddl in (("dead", "ALTER TABLE edge_outbox ADD COLUMN dead INTEGER NOT NULL DEFAULT 0"),
                                ("dead_reason", "ALTER TABLE edge_outbox ADD COLUMN dead_reason TEXT NOT NULL DEFAULT ''")):
                try:
                    db.execute(ddl)
                except sqlite3.OperationalError:
                    pass

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def _session(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, payload: dict[str, Any]) -> int:
        message_id = str(payload["message_id"])
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._session() as db:
            row = db.execute("SELECT id, payload FROM edge_outbox WHERE message_id=?", (message_id,)).fetchone()
            if row:
                if row["payload"] != serialized:
                    raise ValueError(f"message_id {message_id} already exists with a different payload")
                return int(row["id"])
            cur = db.execute("INSERT INTO edge_outbox(message_id, payload) VALUES (?, ?)", (message_id, serialized))
            return int(cur.lastrowid)

    def pending(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._session() as db:
            rows = db.execute(
                "SELECT id, message_id, payload, attempts FROM edge_outbox WHERE dead=0 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete(self, row_id: int) -> None:
        with self._session() as db:
            db.execute("DELETE FROM edge_outbox WHERE id=?", (row_id,))

    def mark_attempt(self, row_id: int, error: str) -> int:
        with self._session() as db:
            db.execute("UPDATE edge_outbox SET attempts=attempts+1, last_error=? WHERE id=?", (error[:1000], row_id))
            row = db.execute("SELECT attempts FROM edge_outbox WHERE id=?", (row_id,)).fetchone()
            return int(row["attempts"]) if row else 0

    def dead_letter(self, row_id: int, reason: str) -> None:
        with self._session() as db:
            db.execute("UPDATE edge_outbox SET dead=1, dead_reason=? WHERE id=?", (reason[:500], row_id))

    def dead_letters(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._session() as db:
            rows = db.execute(
                "SELECT id, message_id, attempts, last_error, dead_reason, created_at FROM edge_outbox WHERE dead=1 ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def attempts(self, row_id: int) -> int:
        with self._session() as db:
            row = db.execute("SELECT attempts FROM edge_outbox WHERE id=?", (row_id,)).fetchone()
            return int(row["attempts"]) if row else 0

    def count(self) -> int:
        """Live (non-dead) messages waiting to be delivered."""
        with self._session() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM edge_outbox WHERE dead=0").fetchone()
            return int(row["n"])

    def stats(self) -> dict[str, Any]:
        with self._session() as db:
            row = db.execute("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM edge_outbox WHERE dead=0").fetchone()
            dead = db.execute("SELECT COUNT(*) AS n FROM edge_outbox WHERE dead=1").fetchone()
            return {"pending": int(row["n"]), "dead": int(dead["n"]), "oldest": row["oldest"]}

    def oldest_created_at(self) -> str | None:
        return self.stats()["oldest"]
