"""Transactional local ingestion generations and resumable sink checkpoints."""

from contextlib import closing, contextmanager
from pathlib import Path
import json
import os
import sqlite3
import uuid


class IngestionLedger:
    """A dependency-free, single-writer SQLite ingestion journal."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS generations (
                    generation TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS records (
                    generation TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    business_hash TEXT NOT NULL,
                    embedding_hash TEXT NOT NULL,
                    metadata_hash TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    PRIMARY KEY (generation, record_id)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    stage TEXT NOT NULL,
                    checkpoint_key TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    payload_hash TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (stage, checkpoint_key, generation)
                );
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_snapshots (
                    source_path TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    valid_json TEXT NOT NULL,
                    invalid_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            generation_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(generations)")
            }
            if "status" not in generation_columns:
                connection.execute(
                    "ALTER TABLE generations ADD COLUMN status TEXT NOT NULL "
                    "DEFAULT 'pending'"
                )
            checkpoint_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(checkpoints)")
            }
            if "payload_hash" not in checkpoint_columns:
                connection.execute(
                    "ALTER TABLE checkpoints ADD COLUMN payload_hash TEXT NOT NULL "
                    "DEFAULT ''"
                )
            snapshot_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(source_snapshots)")
            }
            if "parser_version" not in snapshot_columns:
                connection.execute(
                    "ALTER TABLE source_snapshots ADD COLUMN parser_version "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "partition_summary_json" not in snapshot_columns:
                connection.execute(
                    "ALTER TABLE source_snapshots ADD COLUMN partition_summary_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            if "raw_row_count" not in snapshot_columns:
                connection.execute(
                    "ALTER TABLE source_snapshots ADD COLUMN raw_row_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=0)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def run_lock(self):
        """Hold a process-wide lease for the complete ingestion run.

        SQLite's write transaction protects each journal update, but it cannot be
        held while remote sinks are updated.  An OS advisory lock gives the whole
        pipeline a crash-released, single-writer lease without leaving stale lock
        files after an interrupted process.
        """
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    "Another ingestion run is already active for this ledger."
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @contextmanager
    def writer(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            connection.close()
            raise RuntimeError("Another ingestion writer is already active.") from exc
        writer = _LedgerWriter(connection)
        try:
            yield writer
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def active_generation(self):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM state WHERE key = 'active_generation'"
            ).fetchone()
        return row["value"] if row else None

    def active_records(self):
        generation = self.active_generation()
        if generation is None:
            return []
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM records WHERE generation = ? ORDER BY record_id",
                (generation,),
            ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])} for row in rows
        ]

    def is_checkpoint_complete(
        self, stage, checkpoint_key, generation, payload_hash=None
    ):
        hash_clause = " AND payload_hash = ?" if payload_hash is not None else ""
        values = [stage, checkpoint_key, generation]
        if payload_hash is not None:
            values.append(payload_hash)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM checkpoints WHERE stage = ? AND checkpoint_key = ? "
                f"AND generation = ?{hash_clause}",
                values,
            ).fetchone()
        return row is not None

    def source_snapshot(self, source_path, source_hash, parser_version=None):
        version_clause = " AND parser_version = ?" if parser_version is not None else ""
        values = [source_path, source_hash]
        if parser_version is not None:
            values.append(parser_version)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT valid_json, invalid_json, parser_version, "
                "partition_summary_json, raw_row_count FROM source_snapshots "
                f"WHERE source_path = ? AND source_hash = ?{version_clause}",
                values,
            ).fetchone()
        if row is None:
            return None
        return {
            "valid": json.loads(row["valid_json"]),
            "invalid": json.loads(row["invalid_json"]),
            "parser_version": row["parser_version"],
            "partitions": json.loads(row["partition_summary_json"]),
            "raw_row_count": row["raw_row_count"],
        }


class _LedgerWriter:
    def __init__(self, connection):
        self.connection = connection

    def begin_generation(self, source_hash):
        generation = uuid.uuid4().hex
        self.connection.execute(
            "INSERT INTO generations(generation, source_hash) VALUES (?, ?)",
            (generation, source_hash),
        )
        return generation

    def begin_or_resume_generation(self, source_hash):
        row = self.connection.execute(
            "SELECT generation FROM generations "
            "WHERE source_hash = ? AND status = 'pending' "
            "ORDER BY created_at DESC, generation DESC LIMIT 1",
            (source_hash,),
        ).fetchone()
        return row["generation"] if row else self.begin_generation(source_hash)

    def upsert_record(
        self,
        generation,
        record_id,
        payload,
        *,
        business_hash,
        embedding_hash,
        metadata_hash,
        source_locator,
    ):
        self.connection.execute(
            """
            INSERT OR REPLACE INTO records(
                generation, record_id, payload_json, business_hash,
                embedding_hash, metadata_hash, source_locator
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation,
                record_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                business_hash,
                embedding_hash,
                metadata_hash,
                source_locator,
            ),
        )

    def checkpoint(self, stage, checkpoint_key, generation, payload_hash=""):
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoints("
            "stage, checkpoint_key, generation, payload_hash) VALUES (?, ?, ?, ?)",
            (stage, checkpoint_key, generation, payload_hash),
        )

    def replace_source_snapshots(self, snapshots, active_paths):
        for snapshot in snapshots:
            source_path, source_hash, valid, invalid = snapshot[:4]
            parser_version = snapshot[4] if len(snapshot) > 4 else 0
            partitions = snapshot[5] if len(snapshot) > 5 else []
            raw_row_count = snapshot[6] if len(snapshot) > 6 else 0
            self.connection.execute(
                "INSERT OR REPLACE INTO source_snapshots("
                "source_path, source_hash, valid_json, invalid_json, "
                "parser_version, partition_summary_json, raw_row_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    source_path,
                    source_hash,
                    json.dumps(valid, ensure_ascii=False, default=str),
                    json.dumps(invalid, ensure_ascii=False, default=str),
                    parser_version,
                    json.dumps(partitions, ensure_ascii=False, default=str),
                    raw_row_count,
                ),
            )
        if active_paths:
            placeholders = ",".join("?" for _ in active_paths)
            self.connection.execute(
                f"DELETE FROM source_snapshots WHERE source_path NOT IN ({placeholders})",
                list(active_paths),
            )
        else:
            self.connection.execute("DELETE FROM source_snapshots")

    def activate_generation(self, generation):
        self.connection.execute(
            "UPDATE generations SET status = 'archived' WHERE status = 'active'"
        )
        self.connection.execute(
            "UPDATE generations SET status = 'active' WHERE generation = ?",
            (generation,),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO state(key, value) VALUES ('active_generation', ?)",
            (generation,),
        )
