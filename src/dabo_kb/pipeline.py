from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import SETTINGS
from .acquire import (
    build_processing_queue,
    download_pending,
    fetch_public_candidates,
)
from .openvino_asr import run_in_openvino_environment


def process_batch(
    *,
    limit: int = 10,
    workers: int = 4,
    parallel_workers: int = 2,
) -> dict[str, object]:
    """Run one resumable public-fetch → audio → ASR batch."""
    queue_before = build_processing_queue()
    fetched = fetch_public_candidates(limit=limit, workers=workers)
    downloaded = download_pending(limit=limit)
    transcribed = run_in_openvino_environment(limit=limit, parallel_workers=parallel_workers)
    queue_after = build_processing_queue()
    return {
        "requested": limit,
        "queue_before": queue_before,
        "fetched": fetched,
        "downloaded": downloaded,
        "transcribed": transcribed,
        "queue_after": queue_after,
    }


def _compact_batch(
    number: int,
    before: int,
    outcome: dict[str, object],
) -> dict[str, object]:
    fetched = dict(outcome.get("fetched") or {})
    downloaded = dict(outcome.get("downloaded") or {})
    transcribed = dict(outcome.get("transcribed") or {})
    queue_after = dict(outcome.get("queue_after") or {})
    after = int(queue_after.get("count", before))
    return {
        "batch": number,
        "before": before,
        "after": after,
        "progress": max(0, before - after),
        "resolved": int(fetched.get("resolved", 0)),
        "fetch_failed": int(fetched.get("failed", 0)),
        "downloaded": int(downloaded.get("downloaded", 0)),
        "download_failed": int(downloaded.get("failed", 0)),
        "transcribed": int(transcribed.get("processed", 0)),
        "transcribe_failed": int(transcribed.get("failed", 0)),
    }


def _write_checkpoint(payload: dict[str, object]) -> None:
    path = SETTINGS.sources / "douyin_process_all_checkpoint.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def process_all(
    *,
    batch_size: int = 10,
    workers: int = 4,
    max_stalled_batches: int = 3,
    max_batches: int | None = None,
    count: int | None = None,
    update_artifacts: bool = True,
    parallel_workers: int = 2,
) -> dict[str, object]:
    """Resume all pending items in short-lived media batches."""
    if batch_size < 1:
        raise ValueError("batch_size 必须大于 0")
    if max_stalled_batches < 1:
        raise ValueError("max_stalled_batches 必须大于 0")
    if count is not None and count < 1:
        raise ValueError("count 必须大于 0")

    initial_queue = build_processing_queue()
    initial = int(initial_queue["count"])
    remaining = initial
    requested = initial if count is None else min(count, initial)
    target_remaining = initial - requested
    stalled = 0
    batches: list[dict[str, object]] = []
    started_at = datetime.now(timezone.utc).isoformat()

    while remaining > target_remaining:
        if max_batches is not None and len(batches) >= max_batches:
            break
        outcome = process_batch(
            limit=min(batch_size, remaining - target_remaining),
            workers=workers,
            parallel_workers=parallel_workers,
        )
        batch = _compact_batch(len(batches) + 1, remaining, outcome)
        batches.append(batch)
        remaining = int(batch["after"])
        stalled = stalled + 1 if int(batch["progress"]) == 0 else 0
        checkpoint = {
            "started_at": started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "initial": initial,
            "remaining": remaining,
            "processed": initial - remaining,
            "stalled_batches": stalled,
            "last_batch": batch,
        }
        _write_checkpoint(checkpoint)
        print(json.dumps(batch, ensure_ascii=False), flush=True)
        if stalled >= max_stalled_batches:
            break

    artifacts: dict[str, object] = {}
    if update_artifacts and (initial != remaining or remaining == 0):
        from .graph import build_graph
        from .vector import build_vector_index

        artifacts["index"] = build_vector_index()
        artifacts["graph"] = build_graph()

    result = {
        "initial": initial,
        "requested": requested,
        "processed": initial - remaining,
        "remaining": remaining,
        "batches": len(batches),
        "completed": remaining <= target_remaining,
        "library_completed": remaining == 0,
        "target_remaining": target_remaining,
        "stopped_for_no_progress": (
            remaining > target_remaining and stalled >= max_stalled_batches
        ),
        "last_batch": batches[-1] if batches else None,
        "artifacts": artifacts,
    }
    _write_checkpoint(
        {
            "started_at": started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **result,
        }
    )
    return result
