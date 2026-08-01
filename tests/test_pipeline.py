from __future__ import annotations

from types import SimpleNamespace

from dabo_kb import pipeline


def _outcome(after: int, processed: int) -> dict[str, object]:
    return {
        "fetched": {"resolved": processed, "failed": 0},
        "downloaded": {"downloaded": processed, "failed": 0},
        "transcribed": {"processed": processed, "failed": 0},
        "queue_after": {"count": after},
    }


def test_process_all_runs_short_batches_until_done(tmp_path, monkeypatch):
    outcomes = iter(
        [
            _outcome(after=7, processed=3),
            _outcome(after=4, processed=3),
            _outcome(after=0, processed=4),
        ]
    )
    monkeypatch.setattr(
        pipeline,
        "build_processing_queue",
        lambda: {"count": 10},
    )
    monkeypatch.setattr(
        pipeline,
        "process_batch",
        lambda **_kwargs: next(outcomes),
    )
    monkeypatch.setattr(pipeline, "SETTINGS", SimpleNamespace(sources=tmp_path))

    result = pipeline.process_all(
        batch_size=4,
        update_artifacts=False,
    )

    assert result["processed"] == 10
    assert result["remaining"] == 0
    assert result["batches"] == 3
    assert result["completed"] is True


def test_process_all_stops_after_repeated_no_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "build_processing_queue",
        lambda: {"count": 5},
    )
    monkeypatch.setattr(
        pipeline,
        "process_batch",
        lambda **_kwargs: _outcome(after=5, processed=0),
    )
    monkeypatch.setattr(pipeline, "SETTINGS", SimpleNamespace(sources=tmp_path))

    result = pipeline.process_all(
        batch_size=2,
        max_stalled_batches=2,
        update_artifacts=False,
    )

    assert result["remaining"] == 5
    assert result["batches"] == 2
    assert result["stopped_for_no_progress"] is True


def test_process_all_count_uses_smaller_final_batch(tmp_path, monkeypatch):
    limits: list[int] = []
    remaining = 30

    monkeypatch.setattr(
        pipeline,
        "build_processing_queue",
        lambda: {"count": remaining},
    )

    def fake_process_batch(*, limit: int, workers: int):
        nonlocal remaining
        limits.append(limit)
        remaining -= limit
        return _outcome(after=remaining, processed=limit)

    monkeypatch.setattr(pipeline, "process_batch", fake_process_batch)
    monkeypatch.setattr(pipeline, "SETTINGS", SimpleNamespace(sources=tmp_path))

    result = pipeline.process_all(
        batch_size=10,
        count=26,
        update_artifacts=False,
    )

    assert limits == [10, 10, 6]
    assert result["requested"] == 26
    assert result["processed"] == 26
    assert result["remaining"] == 4
    assert result["target_remaining"] == 4
    assert result["completed"] is True
    assert result["library_completed"] is False
