from __future__ import annotations

from pathlib import Path

from Compressor.decision_memory import DecisionKey, DecisionMemory


def make_key(*, block_hash: str = "a" * 64, candidates: tuple[str, ...] = ("zstd", "bzip2"), dtype: str = "float32") -> DecisionKey:
    return DecisionKey(
        block_hash=block_hash,
        raw_size=4096,
        context={
            "dtype_name": dtype,
            "shape": [1024],
            "typesize": 4,
            "endian": "little",
            "scenario": "science",
        },
        candidates=candidates,
        objective="stored_size",
        runtime_signature="decision-memory-test-v1",
    )


def test_exact_decision_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    key = make_key()
    memory = DecisionMemory(path)
    memory.record(key, selected_pipeline="zstd", ranking=[{"pipeline_name": "zstd", "status": "ok"}])
    memory.close()

    reopened = DecisionMemory(path)
    record = reopened.lookup(key)
    assert record is not None
    assert record.selected_pipeline == "zstd"
    assert record.ranking == [{"pipeline_name": "zstd", "status": "ok"}]
    assert reopened.stats()["decision_count"] == 1
    reopened.close()


def test_exact_decision_misses_when_selection_conditions_change(tmp_path: Path) -> None:
    memory = DecisionMemory(tmp_path / "memory.sqlite3")
    key = make_key()
    memory.record(key, selected_pipeline="zstd", ranking=[])

    assert memory.lookup(key) is not None
    assert memory.lookup(make_key(candidates=("zstd", "lz4"))) is None
    assert memory.lookup(make_key(dtype="float64")) is None
    assert memory.lookup(DecisionKey(
        block_hash=key.block_hash,
        raw_size=key.raw_size,
        context=key.context,
        candidates=key.candidates,
        objective="system_time",
        runtime_signature=key.runtime_signature,
    )) is None
    memory.close()


def test_malformed_entry_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    memory = DecisionMemory(path)
    key = make_key()
    memory.record(key, selected_pipeline="zstd", ranking=[])
    memory.close()

    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE decisions SET selected_pipeline = ''")
        connection.commit()

    reopened = DecisionMemory(path)
    assert reopened.lookup(key) is None
    reopened.close()


def test_schema_one_database_migrates_to_current_schema(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "memory-v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata(name, value) VALUES('schema_version', '1')")
        connection.execute("""
            CREATE TABLE decisions (
                key_hash TEXT PRIMARY KEY, key_json TEXT NOT NULL, block_hash TEXT NOT NULL,
                raw_size INTEGER NOT NULL, selected_pipeline TEXT NOT NULL, ranking_json TEXT NOT NULL,
                created_at TEXT NOT NULL, last_hit_at TEXT, hit_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        connection.commit()

    memory = DecisionMemory(path)
    version = memory.connection.execute("SELECT value FROM metadata WHERE name = 'schema_version'").fetchone()[0]
    columns = {row["name"] for row in memory.connection.execute("PRAGMA table_info(decisions)").fetchall()}
    assert version == "2"
    assert {"similarity_bucket", "similarity_features_json"}.issubset(columns)
    memory.close()


def test_similar_lookup_requires_three_close_consensus_records(tmp_path: Path) -> None:
    memory = DecisionMemory(tmp_path / "memory.sqlite3")
    base = make_key()
    bucket = "features-v1:e8:p4:z0"
    profiles = [
        {"entropy_bits": 4.02 + index * 0.01, "printable_fraction": 0.94, "zero_fraction": 0.01,
         "same_adjacent_fraction": 0.08, "gzip_ratio": 3.2}
        for index in range(3)
    ]
    ranking = [
        {"pipeline_name": "zstd", "status": "ok", "compressed_size": 100, "compress_time_s": 0.1,
         "decompress_time_s": 0.01, "estimated_system_cost_s": 0.12},
        {"pipeline_name": "bzip2", "status": "ok", "compressed_size": 140, "compress_time_s": 0.2,
         "decompress_time_s": 0.02, "estimated_system_cost_s": 0.24},
    ]
    for index, profile in enumerate(profiles):
        key = DecisionKey(
            block_hash=f"{index + 1:064x}", raw_size=base.raw_size, context=base.context,
            candidates=base.candidates, objective=base.objective, runtime_signature=base.runtime_signature,
        )
        memory.record(
            key, selected_pipeline="zstd", ranking=ranking,
            similarity_bucket=bucket, similarity_features=profile,
        )

    held_out = DecisionKey(
        block_hash="f" * 64, raw_size=base.raw_size, context=base.context,
        candidates=base.candidates, objective=base.objective, runtime_signature=base.runtime_signature,
    )
    query_features = {"entropy_bits": 4.04, "printable_fraction": 0.93, "zero_fraction": 0.01,
                      "same_adjacent_fraction": 0.08, "gzip_ratio": 3.15}
    similar = memory.lookup_similar(held_out, similarity_bucket=bucket, similarity_features=query_features)
    assert similar is not None
    assert similar.selected_pipeline == "zstd"
    assert similar.neighbor_count == 3
    assert similar.agreement == 1.0
    assert [row["pipeline_name"] for row in similar.ranking] == ["zstd", "bzip2"]

    far = dict(query_features, entropy_bits=7.8, printable_fraction=0.03, gzip_ratio=1.0)
    assert memory.lookup_similar(held_out, similarity_bucket=bucket, similarity_features=far) is None
    memory.close()


def test_similar_lookup_abstains_when_neighbors_disagree(tmp_path: Path) -> None:
    memory = DecisionMemory(tmp_path / "memory.sqlite3")
    base = make_key()
    bucket = "features-v1:e8:p4:z0"
    features = {"entropy_bits": 4.0, "printable_fraction": 0.94, "zero_fraction": 0.01,
                "same_adjacent_fraction": 0.08, "gzip_ratio": 3.2}
    ranking = [
        {"pipeline_name": "zstd", "status": "ok", "compressed_size": 100, "compress_time_s": 0.1,
         "decompress_time_s": 0.01, "estimated_system_cost_s": 0.12},
        {"pipeline_name": "bzip2", "status": "ok", "compressed_size": 140, "compress_time_s": 0.2,
         "decompress_time_s": 0.02, "estimated_system_cost_s": 0.24},
    ]
    for index, winner in enumerate(("zstd", "zstd", "bzip2", "bzip2")):
        key = DecisionKey(
            block_hash=f"{index + 11:064x}", raw_size=base.raw_size, context=base.context,
            candidates=base.candidates, objective=base.objective, runtime_signature=base.runtime_signature,
        )
        memory.record(key, selected_pipeline=winner, ranking=ranking,
                      similarity_bucket=bucket, similarity_features=features)

    held_out = DecisionKey(
        block_hash="e" * 64, raw_size=base.raw_size, context=base.context,
        candidates=base.candidates, objective=base.objective, runtime_signature=base.runtime_signature,
    )
    assert memory.lookup_similar(held_out, similarity_bucket=bucket, similarity_features=features) is None
    memory.close()
