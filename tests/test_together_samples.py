from __future__ import annotations

from pathlib import Path

from Script.download_together_samples import Candidate, filename_for_url, plan_downloads, urls_for_component


def test_urls_for_component_matches_path_segment() -> None:
    urls = [
        "https://data.together.xyz/redpajama-data-1T/v1.0.0/arxiv/a.jsonl",
        "https://data.together.xyz/redpajama-data-1T/v1.0.0/github/g.jsonl",
        "https://data.together.xyz/redpajama-data-1T/v1.0.0/common_crawl/c.jsonl",
    ]

    assert urls_for_component(urls, "github") == [urls[1]]


def test_filename_for_url_keeps_component_and_leaf() -> None:
    url = "https://data.together.xyz/redpajama-data-1T/v1.0.0/arxiv/arxiv_023827cd-abc.jsonl"

    assert filename_for_url(url) == "redpajama_arxiv_arxiv_023827cd-abc.jsonl"


def test_plan_downloads_skips_existing_and_stops_at_target(tmp_path: Path) -> None:
    done = tmp_path / "redpajama_arxiv_a.jsonl"
    done.write_bytes(b"x" * 10)
    candidates = [
        Candidate("https://data.together.xyz/redpajama-data-1T/v1.0.0/arxiv/a.jsonl", 10),
        Candidate("https://data.together.xyz/redpajama-data-1T/v1.0.0/arxiv/b.jsonl", 20),
        Candidate("https://data.together.xyz/redpajama-data-1T/v1.0.0/arxiv/c.jsonl", 30),
    ]

    planned = plan_downloads(candidates, tmp_path, target_bytes=35)

    assert [item.url.rsplit("/", 1)[1] for item in planned] == ["b.jsonl", "c.jsonl"]


def test_plan_downloads_ignores_temporary_artifacts(tmp_path: Path) -> None:
    (tmp_path / "failed.jsonl.part").write_bytes(b"x" * 100)
    (tmp_path / "sample.jsonl.assembling").write_bytes(b"x" * 100)
    part_dir = tmp_path / ".sample.jsonl.parts"
    part_dir.mkdir()
    (part_dir / "part_000000").write_bytes(b"x" * 100)
    candidates = [
        Candidate("https://data.together.xyz/redpajama-data-1T/v1.0.0/arxiv/a.jsonl", 10),
    ]

    planned = plan_downloads(candidates, tmp_path, target_bytes=10)

    assert planned == candidates
