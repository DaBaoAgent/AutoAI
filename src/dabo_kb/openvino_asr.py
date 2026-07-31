from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .db import connect
from .routing import route_title
from .transcribe import persist_transcript


ROOT = Path(__file__).resolve().parents[2]
PHASE_DIR = ROOT / "benchmarks" / "phase1"
OPENVINO_PYTHON = (
    PHASE_DIR / ".venv-openvino" / "Scripts" / "python.exe"
)
MODEL_PATHS = {
    "base": PHASE_DIR / "models" / "OpenVINO-whisper-base-int8-ov",
    "small": PHASE_DIR / "models" / "OpenVINO-whisper-small-int8-ov",
}


def _ensure_wav(source_id: str) -> Path:
    source = SETTINGS.media / f"{source_id}.m4a"
    if not source.exists():
        raise FileNotFoundError(source)
    target = SETTINGS.tmp / f"{source_id}.openvino.wav"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:] or "WAV conversion failed")
    return target


def _read_wav(wav_path: Path) -> Any:
    import numpy as np

    with wave.open(str(wav_path), "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getframerate() != 16000
            or handle.getsampwidth() != 2
        ):
            raise ValueError(f"Expected mono 16 kHz s16 WAV: {wav_path}")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768


def _duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


class PipelinePool:
    def __init__(self) -> None:
        self._pipelines: dict[str, tuple[object, object]] = {}

    def get(self, lane: str) -> tuple[object, object]:
        if lane in self._pipelines:
            return self._pipelines[lane]
        import openvino_genai as ov_genai

        model_path = MODEL_PATHS[lane]
        if not model_path.exists():
            raise FileNotFoundError(model_path)
        pipeline_class = getattr(
            ov_genai,
            "ASRPipeline",
            ov_genai.WhisperPipeline,
        )
        pipeline = pipeline_class(str(model_path), "CPU")
        config = pipeline.get_generation_config()
        config.language = "<|zh|>"
        config.task = "transcribe"
        config.return_timestamps = True
        config.word_timestamps = False
        self._pipelines[lane] = (pipeline, config)
        return pipeline, config


def _pending_rows(
    *,
    source_id: str | None,
    limit: int | None,
    force: bool,
) -> list[dict[str, str]]:
    where = [
        "source_type='douyin'",
        "is_ai=1",
    ]
    params: list[object] = []
    if source_id:
        where.append("source_id=?")
        params.append(source_id)
    elif not force:
        where.append("status!='transcribed'")
    sql = (
        "SELECT source_id, title, status FROM documents WHERE "
        + " AND ".join(where)
        + " ORDER BY id"
    )
    with connect() as con:
        rows = con.execute(sql, tuple(params)).fetchall()
    ready = [
        dict(row)
        for row in rows
        if (SETTINGS.media / f"{row['source_id']}.m4a").exists()
    ]
    return ready if limit is None else ready[:limit]


def _transcribe_one(row: dict[str, str]) -> dict[str, object]:
    """Transcribe a single item. Module-level for ThreadPoolExecutor."""
    pool = PipelinePool()
    wav_path: Path | None = None
    try:
        route = route_title(row["title"])
        lane = str(route["lane"])
        wav_path = _ensure_wav(row["source_id"])
        raw_speech = _read_wav(wav_path)
        duration_sec = _duration(wav_path)
        pipeline, generation_config = pool.get(lane)
        started = time.perf_counter()
        generated = pipeline.generate(raw_speech, generation_config)
        infer_sec = time.perf_counter() - started
        text = str(generated.texts[0]).strip()
        raw_chunks = generated.chunks or []
        if raw_chunks and isinstance(raw_chunks[0], list):
            raw_chunks = raw_chunks[0]
        segments = [
            {
                "start": round(float(chunk.start_ts), 3),
                "end": round(float(chunk.end_ts), 3),
                "text": str(chunk.text).strip(),
            }
            for chunk in raw_chunks
            if str(chunk.text).strip()
        ]
        if not segments and text:
            segments = [
                {
                    "start": 0.0,
                    "end": round(duration_sec, 3),
                    "text": text,
                }
            ]
        result = persist_transcript(
            row["source_id"],
            segments,
            language="zh",
            engine="openvino-genai",
            model_name=f"whisper-{lane}-int8-ov",
            lane=lane,
            infer_sec=round(infer_sec, 3),
        )
        result.update(
            {
                "id": row["source_id"],
                "duration_sec": round(duration_sec, 3),
                "x_realtime": round(duration_sec / infer_sec, 3),
            }
        )
        return result
    except Exception as exc:
        return {
            "id": row["source_id"],
            "error": str(exc),
            "_failed": True,
        }
    finally:
        if wav_path is not None:
            wav_path.unlink(missing_ok=True)


def transcribe_openvino_batch(
    *,
    source_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
    parallel_workers: int = 2,
) -> dict[str, object]:
    pool = PipelinePool()
    rows = _pending_rows(source_id=source_id, limit=limit, force=force)
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    if parallel_workers <= 1 or len(rows) <= 1:
        # Sequential fallback (original path)
        for index, row in enumerate(rows, start=1):
            wav_path: Path | None = None
            try:
                route = route_title(row["title"])
                lane = str(route["lane"])
                wav_path = _ensure_wav(row["source_id"])
                raw_speech = _read_wav(wav_path)
                duration_sec = _duration(wav_path)
                pipeline, generation_config = pool.get(lane)
                started = time.perf_counter()
                generated = pipeline.generate(raw_speech, generation_config)
                infer_sec = time.perf_counter() - started
                text = str(generated.texts[0]).strip()
                raw_chunks = generated.chunks or []
                if raw_chunks and isinstance(raw_chunks[0], list):
                    raw_chunks = raw_chunks[0]
                segments = [
                    {
                        "start": round(float(chunk.start_ts), 3),
                        "end": round(float(chunk.end_ts), 3),
                        "text": str(chunk.text).strip(),
                    }
                    for chunk in raw_chunks
                    if str(chunk.text).strip()
                ]
                if not segments and text:
                    segments = [
                        {
                            "start": 0.0,
                            "end": round(duration_sec, 3),
                            "text": text,
                        }
                    ]
                result = persist_transcript(
                    row["source_id"],
                    segments,
                    language="zh",
                    engine="openvino-genai",
                    model_name=f"whisper-{lane}-int8-ov",
                    lane=lane,
                    infer_sec=round(infer_sec, 3),
                )
                result.update(
                    {
                        "progress": f"{index}/{len(rows)}",
                        "id": row["source_id"],
                        "duration_sec": round(duration_sec, 3),
                        "x_realtime": round(duration_sec / infer_sec, 3),
                    }
                )
                print(json.dumps(result, ensure_ascii=False), flush=True)
                results.append(result)
            except Exception as exc:
                failure = {
                    "id": row["source_id"],
                    "progress": f"{index}/{len(rows)}",
                    "error": str(exc),
                }
                failures.append(failure)
                print(
                    json.dumps(
                        {"state": "failed", **failure},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            finally:
                if wav_path is not None:
                    wav_path.unlink(missing_ok=True)
    else:
        # Parallel mode with ThreadPoolExecutor
        workers = min(parallel_workers, len(rows), 4)
        completed = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_transcribe_one, row): idx
                for idx, row in enumerate(rows)
            }
            for future in as_completed(futures):
                completed += 1
                try:
                    item = future.result()
                    if item.pop("_failed", False):
                        failures.append(
                            {
                                "id": str(item.get("id", "")),
                                "progress": f"{completed}/{len(rows)}",
                                "error": str(item.get("error", "?")),
                            }
                        )
                        print(
                            json.dumps(
                                {"state": "failed", **failures[-1]},
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                    else:
                        item["progress"] = f"{completed}/{len(rows)}"
                        print(
                            json.dumps(item, ensure_ascii=False), flush=True
                        )
                        results.append(item)
                except Exception as exc:
                    idx = futures[future]
                    failure = {
                        "id": rows[idx]["source_id"],
                        "progress": f"{completed}/{len(rows)}",
                        "error": str(exc),
                    }
                    failures.append(failure)
                    print(
                        json.dumps(
                            {"state": "failed", **failure},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

    return {
        "attempted": len(rows),
        "processed": len(results),
        "failed": len(failures),
        "failures": failures,
        "base": sum(item["lane"] == "base" for item in results),
        "small": sum(item["lane"] == "small" for item in results),
        "needs_visual_review": sum(
            bool(item["needs_visual_review"]) for item in results
        ),
        "characters": sum(int(item["characters"]) for item in results),
    }


def run_in_openvino_environment(
    *,
    source_id: str | None = None,
    limit: int | None = None,
    force: bool = False,
    parallel_workers: int = 2,
) -> dict[str, object]:
    if not OPENVINO_PYTHON.exists():
        raise FileNotFoundError(
            "OpenVINO 独立环境不存在；先按 benchmarks/phase1/README.md 初始化"
        )
    summary_path = SETTINGS.tmp / "openvino_batch_summary.json"
    args = [
        str(OPENVINO_PYTHON),
        "-m",
        "dabo_kb.openvino_asr",
        "--worker",
        "--summary",
        str(summary_path),
    ]
    if source_id:
        args.extend(["--id", source_id])
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if force:
        args.append("--force")
    if parallel_workers > 1:
        args.extend(["--parallel-workers", str(parallel_workers)])
    summary_path.unlink(missing_ok=True)
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT / "src")
        if not existing
        else f"{ROOT / 'src'}{os.pathsep}{existing}"
    )
    completed = subprocess.run(args, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"OpenVINO 转写进程失败，退出码 {completed.returncode}"
        )
    if not summary_path.exists():
        raise FileNotFoundError(summary_path)
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--parallel-workers", type=int, default=2)
    parser.add_argument("--summary", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.worker:
        raise SystemExit("Use dabo-kb transcribe-smart")
    SETTINGS.ensure()
    summary = transcribe_openvino_batch(
        source_id=args.id,
        limit=args.limit,
        force=args.force,
        parallel_workers=args.parallel_workers,
    )
    if args.summary:
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
