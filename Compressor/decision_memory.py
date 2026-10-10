"""Durable memory for CPSS pipeline decisions.

The first version intentionally stores decisions, not compressed payloads.  A
record is valid only when the block bytes and all selection conditions match.
SQLite is the source of truth so the file can be reopened by a later run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported decision-memory value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DecisionKey:
    """All conditions that must match before a decision can be reused."""

    block_hash: str
    raw_size: int
    context: dict[str, Any]
    candidates: tuple[str, ...]
    objective: str
    runtime_signature: str

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "block_hash": str(self.block_hash),
            "raw_size": int(self.raw_size),
            "context": self.context,
            "candidates": list(self.candidates),
            "objective": str(self.objective),
            "runtime_signature": str(self.runtime_signature),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical_json(self.payload()).encode("utf-8")).hexdigest()

    def serialized(self) -> str:
        return _canonical_json(self.payload())


@dataclass(frozen=True)
class DecisionRecord:
    selected_pipeline: str
    ranking: list[dict[str, Any]]
    source: str = "exact_memory"
    hit_count: int = 0
    neighbor_count: int = 0
    agreement: float = 1.0


class DecisionMemory:
    """SQLite-backed exact decision memory.

    Database errors are intentionally allowed to propagate from writes, while
    malformed or missing reads return a miss.  Callers can catch write errors
    and continue with the exhaustive selector without making the memory a
    correctness dependency.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                key_hash TEXT PRIMARY KEY,
                key_json TEXT NOT NULL,
                block_hash TEXT NOT NULL,
                raw_size INTEGER NOT NULL,
                selected_pipeline TEXT NOT NULL,
                ranking_json TEXT NOT NULL,
                similarity_bucket TEXT,
                similarity_features_json TEXT,
                created_at TEXT NOT NULL,
                last_hit_at TEXT,
                hit_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS decisions_block_hash_idx
                ON decisions(block_hash);
            """
        )
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(decisions)").fetchall()
        }
        if "similarity_bucket" not in columns:
            self.connection.execute("ALTER TABLE decisions ADD COLUMN similarity_bucket TEXT")
        if "similarity_features_json" not in columns:
            self.connection.execute("ALTER TABLE decisions ADD COLUMN similarity_features_json TEXT")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS decisions_similarity_bucket_idx ON decisions(similarity_bucket)"
        )
        existing = self.connection.execute(
            "SELECT value FROM metadata WHERE name = 'schema_version'"
        ).fetchone()
        if existing is None:
            self.connection.execute(
                "INSERT INTO metadata(name, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif str(existing["value"]) == "1":
            self.connection.execute(
                "UPDATE metadata SET value = ? WHERE name = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
        elif str(existing["value"]) != str(SCHEMA_VERSION):
            raise RuntimeError(
                f"unsupported decision-memory schema: {existing['value']} (expected {SCHEMA_VERSION})"
            )
        self.connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def lookup(self, key: DecisionKey) -> DecisionRecord | None:
        row = self.connection.execute(
            "SELECT key_json, selected_pipeline, ranking_json, hit_count "
            "FROM decisions WHERE key_hash = ?",
            (key.digest(),),
        ).fetchone()
        if row is None or row["key_json"] != key.serialized():
            return None
        selected = str(row["selected_pipeline"] or "").strip()
        if not selected:
            return None
        try:
            ranking = json.loads(row["ranking_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(ranking, list) or any(not isinstance(item, dict) for item in ranking):
            return None
        hit_count = int(row["hit_count"] or 0) + 1
        self.connection.execute(
            "UPDATE decisions SET hit_count = ?, last_hit_at = ? WHERE key_hash = ?",
            (hit_count, self._now(), key.digest()),
        )
        self.connection.commit()
        return DecisionRecord(
            selected_pipeline=selected,
            ranking=ranking,
            hit_count=hit_count,
        )

    def record(
        self,
        key: DecisionKey,
        *,
        selected_pipeline: str,
        ranking: list[dict[str, Any]],
        similarity_bucket: str | None = None,
        similarity_features: dict[str, Any] | None = None,
    ) -> None:
        selected = str(selected_pipeline or "").strip()
        if not selected:
            raise ValueError("decision-memory records require a selected pipeline")
        if not isinstance(ranking, list) or any(not isinstance(item, dict) for item in ranking):
            raise TypeError("decision-memory ranking must be a list of dictionaries")
        now = self._now()
        self.connection.execute(
            """
            INSERT INTO decisions(
                key_hash, key_json, block_hash, raw_size, selected_pipeline,
                ranking_json, similarity_bucket, similarity_features_json,
                created_at, last_hit_at, hit_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
            ON CONFLICT(key_hash) DO UPDATE SET
                key_json = excluded.key_json,
                block_hash = excluded.block_hash,
                raw_size = excluded.raw_size,
                selected_pipeline = excluded.selected_pipeline,
                ranking_json = excluded.ranking_json,
                similarity_bucket = excluded.similarity_bucket,
                similarity_features_json = excluded.similarity_features_json,
                created_at = excluded.created_at
            """,
            (
                key.digest(),
                key.serialized(),
                str(key.block_hash),
                int(key.raw_size),
                selected,
                _canonical_json(ranking),
                str(similarity_bucket) if similarity_bucket else None,
                _canonical_json(similarity_features) if similarity_features is not None else None,
                now,
            ),
        )
        self.connection.commit()

    def lookup_similar(
        self,
        key: DecisionKey,
        *,
        similarity_bucket: str,
        similarity_features: dict[str, Any],
        max_neighbors: int = 16,
        min_neighbors: int = 3,
        min_agreement: float = 0.8,
        max_distance: float = 0.75,
    ) -> DecisionRecord | None:
        """Find a conservative consensus among compatible nearby feature rows."""
        from .decision_features import feature_distance

        rows = self.connection.execute(
            "SELECT key_json, selected_pipeline, ranking_json, similarity_features_json "
            "FROM decisions WHERE similarity_bucket = ? ORDER BY created_at DESC LIMIT ?",
            (str(similarity_bucket), max(1, int(max_neighbors) * 8)),
        ).fetchall()
        nearby: list[tuple[float, str, list[dict[str, Any]]]] = []
        for row in rows:
            try:
                payload = json.loads(row["key_json"])
                features = json.loads(row["similarity_features_json"])
                ranking = json.loads(row["ranking_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or not isinstance(features, dict) or not isinstance(ranking, list):
                continue
            expected = key.payload()
            compatibility_fields = (
                "schema_version", "raw_size", "context", "candidates", "objective", "runtime_signature",
            )
            if any(payload.get(field) != expected.get(field) for field in compatibility_fields):
                continue
            if payload.get("block_hash") == key.block_hash:
                continue
            try:
                distance = feature_distance(similarity_features, features)
            except (KeyError, TypeError, ValueError):
                continue
            selected = str(row["selected_pipeline"] or "")
            if selected in key.candidates and distance <= max_distance:
                nearby.append((distance, selected, ranking))
        nearby.sort(key=lambda item: (item[0], item[1]))
        nearby = nearby[:max(1, int(max_neighbors))]
        if len(nearby) < max(1, int(min_neighbors)):
            return None
        counts: dict[str, int] = {}
        for _distance, selected, _ranking in nearby:
            counts[selected] = counts.get(selected, 0) + 1
        selected, winner_count = max(counts.items(), key=lambda item: (item[1], item[0]))
        agreement = winner_count / len(nearby)
        if agreement < float(min_agreement):
            return None
        reference_ranking = next(ranking for _distance, pipeline, ranking in nearby if pipeline == selected)
        ranking_by_name = {str(row.get("pipeline_name")): row for row in reference_ranking}
        for candidate in key.candidates:
            peer_rows = [
                next((row for row in ranking if str(row.get("pipeline_name")) == candidate), None)
                for _distance, _winner, ranking in nearby
            ]
            valid = [row for row in peer_rows if row and row.get("status") == "ok"]
            if valid:
                averaged: dict[str, Any] = {"pipeline_name": candidate, "status": "ok", "error": "similar_memory_consensus"}
                for metric in ("compressed_size", "compressed_bytes", "compress_time_s", "decompress_time_s", "estimated_system_cost_s"):
                    values = [float(row[metric]) for row in valid if row.get(metric) not in (None, "")]
                    if values:
                        averaged[metric] = statistics.median(values)
                ranking_by_name[candidate] = averaged
            else:
                ranking_by_name[candidate] = {"pipeline_name": candidate, "status": "failed", "error": "no_neighbor_measurement"}
        ranking = [ranking_by_name[candidate] for candidate in key.candidates]
        self.connection.commit()
        return DecisionRecord(
            selected_pipeline=selected,
            ranking=ranking,
            source="similar_memory",
            neighbor_count=len(nearby),
            agreement=agreement,
        )

    def stats(self) -> dict[str, int]:
        row = self.connection.execute(
            "SELECT COUNT(*) AS decision_count, COALESCE(SUM(hit_count), 0) AS hit_count FROM decisions"
        ).fetchone()
        return {
            "decision_count": int(row["decision_count"]),
            "hit_count": int(row["hit_count"]),
        }

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DecisionMemory":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
