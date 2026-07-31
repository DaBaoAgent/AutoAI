from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PHASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = ROOT / "data" / "media"
SAMPLES_PATH = PHASE_DIR / "samples.json"


def load_samples(profile: str) -> list[dict[str, Any]]:
    payload = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    samples = payload["samples"]
    if profile == "quick":
        samples = [sample for sample in samples if sample.get("smoke")][:1]
    elif profile == "smoke":
        samples = [sample for sample in samples if sample.get("smoke")]
    return samples


def normalize_term(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def term_recall(text: str, terms: list[str]) -> dict[str, Any]:
    normalized_text = normalize_term(text)
    hits = [term for term in terms if normalize_term(term) in normalized_text]
    return {
        "expected": terms,
        "hits": hits,
        "recall": round(len(hits) / len(terms), 4) if terms else None,
    }


def run_faster_whisper(
    samples: list[dict[str, Any]],
    output_dir: Path,
    model_name: str,
    cpu_threads: int,
    force: bool,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    load_started = time.perf_counter()
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
    )
    load_sec = time.perf_counter() - load_started
    results: list[dict[str, Any]] = []

    for sample in samples:
        source_id = sample["id"]
        output_path = output_dir / f"{source_id}.json"
        if output_path.exists() and not force:
            results.append(json.loads(output_path.read_text(encoding="utf-8")))
            print(f"RESUME {source_id}", flush=True)
            continue

        audio_path = MEDIA_DIR / f"{source_id}.m4a"
        if not audio_path.exists():
            raise FileNotFoundError(audio_path)

        started = time.perf_counter()
        segments_iter, info = model.transcribe(
            str(audio_path),
            language="zh",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=True,
        )
        segments = [
            {
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
            }
            for segment in segments_iter
            if segment.text.strip()
        ]
        infer_sec = time.perf_counter() - started
        text = "\n".join(segment["text"] for segment in segments)
        result = {
            "id": source_id,
            "engine": "faster-whisper",
            "model": model_name,
            "duration_sec": sample["duration_sec"],
            "infer_sec": round(infer_sec, 3),
            "x_realtime": round(sample["duration_sec"] / infer_sec, 3),
            "language": info.language,
            "language_probability": round(info.language_probability, 5),
            "characters": len(text),
            "segments": segments,
            "text": text,
            "term_score": term_recall(text, sample.get("expected_terms", [])),
        }
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(result)
        print(
            f"DONE {source_id} {result['x_realtime']}x "
            f"terms={len(result['term_score']['hits'])}/"
            f"{len(result['term_score']['expected'])}",
            flush=True,
        )

    return summarize("faster-whisper", model_name, load_sec, results)


def ensure_wav(source_id: str) -> tuple[Path, float]:
    wav_dir = PHASE_DIR / "cache" / "wav16k"
    wav_dir.mkdir(parents=True, exist_ok=True)
    wav_path = wav_dir / f"{source_id}.wav"
    if wav_path.exists():
        return wav_path, 0.0
    source_path = MEDIA_DIR / f"{source_id}.m4a"
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    started = time.perf_counter()
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        check=True,
    )
    return wav_path, time.perf_counter() - started


def _whisper_cpp_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in payload.get("transcription", []):
        offsets = segment.get("offsets", {})
        rows.append(
            {
                "start": round(float(offsets.get("from", 0)) / 1000, 3),
                "end": round(float(offsets.get("to", 0)) / 1000, 3),
                "text": str(segment.get("text", "")).strip(),
            }
        )
    return [row for row in rows if row["text"]]


def run_whisper_cpp(
    samples: list[dict[str, Any]],
    output_dir: Path,
    model_name: str,
    cli_path: Path,
    model_path: Path,
    cpu_threads: int,
    force: bool,
) -> dict[str, Any]:
    if not cli_path.exists():
        raise FileNotFoundError(cli_path)
    if not model_path.exists():
        raise FileNotFoundError(model_path)

    results: list[dict[str, Any]] = []
    load_sec = 0.0
    for sample in samples:
        source_id = sample["id"]
        output_path = output_dir / f"{source_id}.json"
        if output_path.exists() and not force:
            results.append(json.loads(output_path.read_text(encoding="utf-8")))
            print(f"RESUME {source_id}", flush=True)
            continue

        wav_path, convert_sec = ensure_wav(source_id)
        raw_prefix = output_dir / f"{source_id}.whisper-cpp"
        raw_json = raw_prefix.with_suffix(".whisper-cpp.json")
        started = time.perf_counter()
        completed = subprocess.run(
            [
                str(cli_path),
                "-m",
                str(model_path),
                "-f",
                str(wav_path),
                "-l",
                "zh",
                "-t",
                str(cpu_threads),
                "-bs",
                "1",
                "-bo",
                "1",
                "-oj",
                "-ojf",
                "-of",
                str(raw_prefix),
                "-np",
                "-ng",
            ],
            cwd=cli_path.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        infer_sec = time.perf_counter() - started
        if completed.returncode != 0:
            raise RuntimeError(
                f"whisper.cpp failed for {source_id}: "
                f"{completed.stderr[-4000:]}"
            )
        if not raw_json.exists():
            raise FileNotFoundError(raw_json)
        raw_payload = json.loads(raw_json.read_text(encoding="utf-8"))
        segments = _whisper_cpp_segments(raw_payload)
        text = "\n".join(segment["text"] for segment in segments)
        result = {
            "id": source_id,
            "engine": "whisper.cpp",
            "model": model_name,
            "duration_sec": sample["duration_sec"],
            "convert_sec": round(convert_sec, 3),
            "infer_sec": round(infer_sec, 3),
            "x_realtime": round(sample["duration_sec"] / infer_sec, 3),
            "language": raw_payload.get("result", {}).get("language", "zh"),
            "characters": len(text),
            "segments": segments,
            "text": text,
            "term_score": term_recall(text, sample.get("expected_terms", [])),
        }
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(result)
        print(
            f"DONE {source_id} {result['x_realtime']}x "
            f"terms={len(result['term_score']['hits'])}/"
            f"{len(result['term_score']['expected'])}",
            flush=True,
        )

    return summarize("whisper.cpp", model_name, load_sec, results)


def read_wav_float(wav_path: Path) -> Any:
    import numpy as np

    with wave.open(str(wav_path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != 16000:
            raise ValueError(f"Expected mono 16 kHz WAV: {wav_path}")
        if handle.getsampwidth() != 2:
            raise ValueError(f"Expected signed 16-bit WAV: {wav_path}")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768


def run_openvino(
    samples: list[dict[str, Any]],
    output_dir: Path,
    model_name: str,
    model_path: Path,
    device: str,
    force: bool,
) -> dict[str, Any]:
    import openvino_genai as ov_genai

    if not model_path.exists():
        raise FileNotFoundError(model_path)
    options: dict[str, Any] = {}
    if "GPU" in device or device == "NPU":
        cache_dir = PHASE_DIR / "cache" / f"openvino-{device.casefold()}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        options["CACHE_DIR"] = str(cache_dir)

    load_started = time.perf_counter()
    pipeline_class = getattr(
        ov_genai,
        "ASRPipeline",
        ov_genai.WhisperPipeline,
    )
    pipe = pipeline_class(str(model_path), device, **options)
    load_sec = time.perf_counter() - load_started
    config = pipe.get_generation_config()
    config.language = "<|zh|>"
    config.task = "transcribe"
    config.return_timestamps = True
    config.word_timestamps = False

    results: list[dict[str, Any]] = []
    for sample in samples:
        source_id = sample["id"]
        output_path = output_dir / f"{source_id}.json"
        if output_path.exists() and not force:
            results.append(json.loads(output_path.read_text(encoding="utf-8")))
            print(f"RESUME {source_id}", flush=True)
            continue

        wav_path, convert_sec = ensure_wav(source_id)
        raw_speech = read_wav_float(wav_path)
        started = time.perf_counter()
        generated = pipe.generate(raw_speech, config)
        infer_sec = time.perf_counter() - started
        text = generated.texts[0].strip()
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
        result = {
            "id": source_id,
            "engine": "openvino-genai",
            "device": device,
            "model": model_name,
            "duration_sec": sample["duration_sec"],
            "convert_sec": round(convert_sec, 3),
            "infer_sec": round(infer_sec, 3),
            "x_realtime": round(sample["duration_sec"] / infer_sec, 3),
            "language": "zh",
            "characters": len(text),
            "segments": segments,
            "text": text,
            "term_score": term_recall(text, sample.get("expected_terms", [])),
        }
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(result)
        print(
            f"DONE {source_id} {result['x_realtime']}x "
            f"terms={len(result['term_score']['hits'])}/"
            f"{len(result['term_score']['expected'])}",
            flush=True,
        )

    return summarize(
        f"openvino-genai-{device}",
        model_name,
        load_sec,
        results,
    )


def summarize(
    engine: str,
    model: str,
    load_sec: float,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    total_audio = sum(item["duration_sec"] for item in results)
    total_infer = sum(item["infer_sec"] for item in results)
    expected = sum(
        len(item["term_score"]["expected"])
        for item in results
    )
    hits = sum(len(item["term_score"]["hits"]) for item in results)
    return {
        "engine": engine,
        "model": model,
        "load_sec": round(load_sec, 3),
        "samples": len(results),
        "audio_sec": round(total_audio, 3),
        "infer_sec": round(total_infer, 3),
        "x_realtime": round(total_audio / total_infer, 3),
        "expected_terms": expected,
        "term_hits": hits,
        "term_recall": round(hits / expected, 4) if expected else None,
        "results": [
            {
                key: item[key]
                for key in (
                    "id",
                    "duration_sec",
                    "infer_sec",
                    "x_realtime",
                    "characters",
                    "term_score",
                )
            }
            for item in results
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--engine",
        choices=["faster-whisper", "whisper.cpp", "openvino"],
        required=True,
    )
    parser.add_argument(
        "--profile",
        choices=["quick", "smoke", "full"],
        default="smoke",
    )
    parser.add_argument("--model", default="small")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--whisper-cli",
        type=Path,
        default=PHASE_DIR / "tools" / "whisper.cpp" / "Release" / "whisper-cli.exe",
    )
    parser.add_argument(
        "--whisper-model",
        type=Path,
        default=PHASE_DIR / "models" / "ggml-small.bin",
    )
    parser.add_argument("--openvino-device", default="CPU")
    parser.add_argument(
        "--openvino-model",
        type=Path,
        default=PHASE_DIR / "models" / "OpenVINO-whisper-small-int8-ov",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    samples = load_samples(args.profile)
    if args.max_samples is not None:
        samples = samples[: args.max_samples]
    engine_label = args.engine
    if args.engine == "openvino":
        engine_label = f"openvino-{args.openvino_device.casefold()}"
    output_dir = PHASE_DIR / "results" / f"{engine_label}-{args.model}-{args.profile}"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.engine == "faster-whisper":
        summary = run_faster_whisper(
            samples,
            output_dir,
            args.model,
            args.cpu_threads,
            args.force,
        )
    elif args.engine == "whisper.cpp":
        summary = run_whisper_cpp(
            samples,
            output_dir,
            args.model,
            args.whisper_cli,
            args.whisper_model,
            args.cpu_threads,
            args.force,
        )
    elif args.engine == "openvino":
        summary = run_openvino(
            samples,
            output_dir,
            f"{args.model}-int8",
            args.openvino_model,
            args.openvino_device,
            args.force,
        )
    else:
        raise ValueError(args.engine)

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
