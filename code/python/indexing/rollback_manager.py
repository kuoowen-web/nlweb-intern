"""
Rollback Manager for M0 Indexing Module.

Manages migration records and supports rollback operations.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class MigrationRecord:
    """Record of a migration operation."""
    migration_id: str
    site: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    old_point_ids: list[str] = field(default_factory=list)
    new_chunk_ids: list[str] = field(default_factory=list)
    status: str = 'in_progress'  # 'in_progress', 'completed', 'rolled_back'


class RollbackManager:
    """
    Manages migration records for safe rollback.

    Uses SQLite for migration tracking and payload backup.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS migration_records (
        migration_id TEXT PRIMARY KEY,
        site TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL,
        old_point_ids_json TEXT,
        new_chunk_ids_json TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_site ON migration_records(site);
    CREATE INDEX IF NOT EXISTS idx_status ON migration_records(status);

    CREATE TABLE IF NOT EXISTS qdrant_backup (
        point_id TEXT PRIMARY KEY,
        migration_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (migration_id) REFERENCES migration_records(migration_id)
    );

    CREATE INDEX IF NOT EXISTS idx_backup_migration ON qdrant_backup(migration_id);
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize RollbackManager.

        Args:
            db_path: Path to migrations database.
                    If None, uses data/indexing/migrations.db
        """
        if db_path is None:
            db_path = Path(__file__).parents[3] / "data" / "indexing" / "migrations.db"

        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False for async compatibility
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(self.SCHEMA)
        return self._conn

    def start_migration(self, site: str) -> str:
        """
        Start a new migration.

        Args:
            site: Site identifier

        Returns:
            Migration ID
        """
        migration_id = str(uuid.uuid4())
        conn = self._get_connection()

        conn.execute("""
            INSERT INTO migration_records
            (migration_id, site, started_at, status, old_point_ids_json, new_chunk_ids_json)
            VALUES (?, ?, ?, 'in_progress', '[]', '[]')
        """, (migration_id, site, datetime.utcnow().isoformat()))
        conn.commit()

        return migration_id

    def record_old_points(self, migration_id: str, point_ids: list[str]) -> None:
        """Record old point IDs for potential rollback."""
        conn = self._get_connection()
        conn.execute("""
            UPDATE migration_records
            SET old_point_ids_json = ?
            WHERE migration_id = ?
        """, (json.dumps(point_ids), migration_id))
        conn.commit()

    def backup_payloads(self, migration_id: str, payloads: list[dict]) -> None:
        """
        Backup Qdrant payloads for rollback.

        Args:
            migration_id: Migration ID
            payloads: List of {point_id, payload} dicts
        """
        conn = self._get_connection()
        now = datetime.utcnow().isoformat()

        conn.executemany("""
            INSERT OR REPLACE INTO qdrant_backup
            (point_id, migration_id, payload_json, created_at)
            VALUES (?, ?, ?, ?)
        """, [
            (p['point_id'], migration_id, json.dumps(p['payload'], ensure_ascii=False), now)
            for p in payloads
        ])
        conn.commit()

    def complete_migration(self, migration_id: str, new_chunk_ids: list[str]) -> None:
        """
        Mark migration as completed.

        Args:
            migration_id: Migration ID
            new_chunk_ids: List of new chunk IDs created
        """
        conn = self._get_connection()
        conn.execute("""
            UPDATE migration_records
            SET status = 'completed',
                completed_at = ?,
                new_chunk_ids_json = ?
            WHERE migration_id = ?
        """, (
            datetime.utcnow().isoformat(),
            json.dumps(new_chunk_ids),
            migration_id
        ))
        conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> MigrationRecord:
        """Convert a database row to MigrationRecord."""
        return MigrationRecord(
            migration_id=row['migration_id'],
            site=row['site'],
            started_at=datetime.fromisoformat(row['started_at']),
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            old_point_ids=json.loads(row['old_point_ids_json'] or '[]'),
            new_chunk_ids=json.loads(row['new_chunk_ids_json'] or '[]'),
            status=row['status']
        )

    def get_migration(self, migration_id: str) -> Optional[MigrationRecord]:
        """Get migration record by ID."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM migration_records WHERE migration_id = ?",
            (migration_id,)
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row else None

    def get_backup_payloads(self, migration_id: str) -> list[dict]:
        """Get backed up payloads for a migration."""
        conn = self._get_connection()
        cursor = conn.execute("""
            SELECT point_id, payload_json FROM qdrant_backup
            WHERE migration_id = ?
        """, (migration_id,))

        return [
            {'point_id': row['point_id'], 'payload': json.loads(row['payload_json'])}
            for row in cursor.fetchall()
        ]

    def mark_rolled_back(self, migration_id: str) -> None:
        """Mark migration as rolled back."""
        conn = self._get_connection()
        conn.execute("""
            UPDATE migration_records
            SET status = 'rolled_back'
            WHERE migration_id = ?
        """, (migration_id,))
        conn.commit()

    def get_migrations_by_site(self, site: str) -> list[MigrationRecord]:
        """Get all migrations for a site, ordered by most recent first."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM migration_records WHERE site = ? ORDER BY started_at DESC",
            (site,)
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def cleanup_old_backups(self, days: int = 30) -> int:
        """
        Delete backup records older than specified days.

        Returns the number of records deleted.
        """
        conn = self._get_connection()
        cursor = conn.execute("""
            DELETE FROM qdrant_backup
            WHERE migration_id IN (
                SELECT migration_id FROM migration_records
                WHERE status IN ('completed', 'rolled_back')
                AND datetime(completed_at) < datetime('now', ?)
            )
        """, (f'-{days} days',))
        conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
