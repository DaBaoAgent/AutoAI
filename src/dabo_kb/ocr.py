from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .config import SETTINGS
from .db import connect, utcnow


LATIN = re.compile(r"\b[A-Za-z][A-Za-z0-9_.+-]{2,}\b")
GITHUB_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com[/\s]+"
    r"([A-Za-z0-9_.-]{2,}[A-Za-z0-9])[/\s]+"
    r"([A-Za-z0-9_.-]{1,}[A-Za-z0-9])(?![A-Za-z0-9_.-])",
    re.IGNORECASE,
)
REPO = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.-]{1,}/[A-Za-z][A-Za-z0-9_.-]{2,})\b"
)
REPO_HEADER = re.compile(
    r"\b([A-Za-z0-9_.-]{2,}/[A-Za-z0-9_.-]{2,})\s+Public\b",
    re.IGNORECASE,
)
LABELED_PROJECT = re.compile(
    r"第[一二三四五六七八九十\d]+个\s*[:：]\s*"
    r"([A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z][A-Za-z0-9_.+-]*){0,3})"
)
RANKED_PROJECT = re.compile(
    r"第[一二三四五六七八九十百\d]+名\s*"
    r"([A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z][A-Za-z0-9_.+-]*){0,3})"
)
ASCII_PROJECT_LINE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z][A-Za-z0-9_.+-]*){0,3}$"
)
PROJECT_UI_WORDS = {
    "code",
    "issues",
    "pull requests",
    "actions",
    "public",
    "notifications",
    "insights",
    "main",
}
RANK_INTRO = re.compile(
    r"第(?:[一二三四五六七八九十百\d]+)名|"
    r"第(?:[一二三四五六七八九十百\d]+)个(?:项目|工具)|"
    r"最后一个(?:项目|工具)|"
    r"项目名称|"
    r"(?:排名|榜单).{0,6}(?:第|前)|重头戏"
)


def _stamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _repository_evidence(
    records: list[dict], pending_names: list[str]
) -> dict[str, dict]:
    """Only accept visual evidence tied to GitHub or an exact pending candidate."""
    evidence: dict[str, dict] = {}
    pending = {name.casefold(): name for name in pending_names}
    for record in records:
        frame_text = " ".join(item["text"] for item in record["lines"])
        folded = frame_text.casefold()
        names: set[str] = set()

        for owner, repository in GITHUB_URL.findall(frame_text):
            names.add(f"{owner}/{repository}".rstrip(".,;:"))
        names.update(REPO_HEADER.findall(frame_text))

        for key, original in pending.items():
            if key in folded:
                names.add(original)

        for name in names:
            if "/" not in name:
                continue
            evidence[name.casefold()] = {
                "name": name,
                "time": record["time"],
                "frame": record["frame"],
            }
    return evidence


def _project_evidence(records: list[dict]) -> dict[str, dict]:
    evidence: dict[str, dict] = {}
    for index, record in enumerate(records):
        for line in record["lines"]:
            for name in LABELED_PROJECT.findall(line["text"]):
                canonical = " ".join(name.split()).rstrip(".,;:")
                variants = []
                for candidate_line in record["lines"]:
                    variants.extend(
                        match.group(0)
                        for match in re.finditer(
                            re.escape(canonical),
                            candidate_line["text"],
                            re.IGNORECASE,
                        )
                    )
                if variants:
                    canonical = max(
                        variants,
                        key=lambda value: (
                            sum(char.isupper() for char in value),
                            len(value),
                        ),
                    )
                evidence[canonical.casefold()] = {
                    "name": canonical,
                    "time": record["time"],
                    "frame": record["frame"],
                }
            for rough_name in RANKED_PROJECT.findall(line["text"]):
                variants = [rough_name]
                nearby = [record]
                if (
                    index + 1 < len(records)
                    and records[index + 1]["time"] - record["time"] <= 3
                ):
                    nearby.append(records[index + 1])
                ascii_lines = []
                for candidate_record in nearby:
                    for candidate_line in candidate_record["lines"]:
                        value = " ".join(candidate_line["text"].split())
                        if (
                            ASCII_PROJECT_LINE.fullmatch(value)
                            and value.casefold() not in PROJECT_UI_WORDS
                        ):
                            ascii_lines.append(value)
                rough_key = re.sub(r"[^a-z0-9]", "", rough_name.casefold())
                for value in ascii_lines:
                    value_key = re.sub(r"[^a-z0-9]", "", value.casefold())
                    if value_key == rough_key:
                        variants.append(value)
                for length in (2, 3):
                    for start in range(0, len(ascii_lines) - length + 1):
                        value = " ".join(ascii_lines[start : start + length])
                        value_key = re.sub(r"[^a-z0-9]", "", value.casefold())
                        if value_key.startswith(rough_key) and len(value_key) <= (
                            len(rough_key) + 3
                        ):
                            variants.append(value)
                canonical = max(
                    variants,
                    key=lambda value: (
                        len(re.sub(r"[^a-z0-9]", "", value.casefold())),
                        sum(char.isupper() for char in value),
                    ),
                ).rstrip(".,;:")
                evidence[canonical.casefold()] = {
                    "name": canonical,
                    "time": record["time"],
                    "frame": record["frame"],
                }
    return evidence


def _filter_redundant_projects(
    projects: dict[str, dict],
    repositories: dict[str, dict],
) -> dict[str, dict]:
    repository_names = []
    for item in repositories.values():
        name = item["name"].split("/", 1)[-1]
        normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
        if normalized:
            repository_names.append(normalized)
    filtered = {}
    for key, item in projects.items():
        normalized = re.sub(r"[^a-z0-9]", "", item["name"].casefold())
        if any(
            normalized.startswith(repository)
            or repository.startswith(normalized)
            for repository in repository_names
        ):
            continue
        filtered[key] = item
    return filtered


def _store_visual_evidence(
    source_id: str, records: list[dict], *, replace_visual: bool = True
) -> dict[str, int]:
    now = utcnow()
    with connect() as con:
        doc = con.execute(
            "SELECT id FROM documents WHERE source_type='douyin' AND source_id=?",
            (source_id,),
        ).fetchone()
        if not doc:
            raise ValueError(f"未找到文档：{source_id}")

        pending_names = [
            row["surface_form"]
            for row in con.execute(
                """
                SELECT surface_form
                FROM name_verifications
                WHERE document_id=? AND status='pending'
                """,
                (doc["id"],),
            )
        ]
        if replace_visual:
            con.execute(
                """
                DELETE FROM name_verifications
                WHERE document_id=? AND evidence_type='video_ocr'
                """,
                (doc["id"],),
            )

        repository_evidence = _repository_evidence(records, pending_names)
        project_evidence = _filter_redundant_projects(
            _project_evidence(records),
            repository_evidence,
        )
        for kind, items in (
            ("repository", repository_evidence),
            ("project", project_evidence),
        ):
            for item in items.values():
                con.execute(
                    """
                    INSERT INTO name_verifications(
                      document_id, surface_form, canonical_name, kind,
                      evidence_type, evidence_ref, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'video_ocr', ?, 'verified', ?, ?)
                    ON CONFLICT(document_id, surface_form, kind) DO UPDATE SET
                      canonical_name=excluded.canonical_name,
                      evidence_type='video_ocr',
                      evidence_ref=excluded.evidence_ref,
                      status='verified',
                      updated_at=excluded.updated_at
                    """,
                    (
                        doc["id"],
                        item["name"],
                        item["name"],
                        kind,
                        f"{item['frame']}@{_stamp(item['time'])}",
                        now,
                        now,
                    ),
                )
    return {
        "verified_repositories": len(repository_evidence),
        "verified_projects": len(project_evidence),
    }


def reverify_ocr(source_id: str) -> dict:
    json_path = SETTINGS.transcripts / f"{source_id}.ocr.json"
    if not json_path.exists():
        raise FileNotFoundError(f"缺少 OCR 结果：{json_path}")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    counts = _store_visual_evidence(source_id, payload["records"])
    return {"id": source_id, **counts}


def _transcript_target_times(source_id: str) -> list[float]:
    transcript = SETTINGS.transcripts / f"{source_id}.json"
    if not transcript.exists():
        return []
    payload = json.loads(transcript.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    times: set[float] = set()
    for index, segment in enumerate(segments):
        if not RANK_INTRO.search(str(segment.get("text") or "")):
            continue
        start = float(segment.get("start") or 0)
        times.add(round(start + 0.5, 1))
        if index + 1 < len(segments):
            following = float(segments[index + 1].get("start") or start)
            if following - start <= 5:
                times.add(round(following + 0.5, 1))
    return sorted(times)


def _extract_targeted_frames(
    video: Path,
    frame_dir: Path,
    times: list[float],
) -> list[tuple[Path, float]]:
    fps = 2
    frame_numbers = [max(0, round(timestamp * fps)) for timestamp in times]
    select = "+".join(f"eq(n\\,{number})" for number in frame_numbers)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"fps={fps},select='{select}',scale='min(1280,iw)':-2",
            "-fps_mode",
            "vfr",
            "-q:v",
            "3",
            str(frame_dir / "%06d.jpg"),
            "-loglevel",
            "error",
        ],
        check=True,
    )
    frames = sorted(frame_dir.glob("*.jpg"))
    return list(zip(frames, times[: len(frames)]))


def ocr_video(
    source_id: str,
    interval: int = 10,
    *,
    keep_video: bool = False,
    targeted: bool = False,
) -> dict:
    video = SETTINGS.media / f"{source_id}-video.mp4"
    if not video.exists():
        raise FileNotFoundError(f"缺少临时视频文件：{video}")

    with connect() as con:
        doc = con.execute(
            "SELECT * FROM documents WHERE source_type='douyin' AND source_id=?",
            (source_id,),
        ).fetchone()
    if not doc:
        raise ValueError(f"未找到文档：{source_id}")

    frame_dir = SETTINGS.root / "data" / "frames" / source_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("*.jpg"):
        old.unlink()

    target_times = _transcript_target_times(source_id) if targeted else []
    if target_times:
        frame_times = _extract_targeted_frames(video, frame_dir, target_times)
        extraction_mode = "transcript_rank_timestamps"
    else:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video),
                "-vf",
                f"fps=1/{interval},scale='min(1280,iw)':-2",
                "-q:v",
                "3",
                str(frame_dir / "%06d.jpg"),
                "-loglevel",
                "error",
            ],
            check=True,
        )
        frame_times = [
            (frame, index * interval)
            for index, frame in enumerate(sorted(frame_dir.glob("*.jpg")))
        ]
        extraction_mode = f"fixed_interval_{interval}s"

    from rapidocr import RapidOCR

    engine = RapidOCR()
    records = []
    seen_lines: set[str] = set()
    for frame, timestamp in frame_times:
        result = engine(str(frame))
        lines = []
        for text, score in zip(result.txts or (), result.scores or ()):
            clean = " ".join(text.split())
            if score < 0.55 or len(clean) < 2:
                continue
            key = clean.casefold()
            if key in seen_lines:
                continue
            seen_lines.add(key)
            lines.append({"text": clean, "score": round(float(score), 4)})
        if lines:
            records.append(
                {
                    "time": timestamp,
                    "stamp": _stamp(timestamp),
                    "frame": str(frame),
                    "lines": lines,
                }
            )
        if not lines or not any(LATIN.search(item["text"]) for item in lines):
            frame.unlink()

    json_path = SETTINGS.transcripts / f"{source_id}.ocr.json"
    md_path = SETTINGS.transcripts / f"{source_id}.ocr.md"
    json_path.write_text(
        json.dumps(
            {
                "id": source_id,
                "title": doc["title"],
                "url": doc["url"],
                "interval_seconds": interval,
                "extraction_mode": extraction_mode,
                "sampled_frames": len(frame_times),
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    md_lines = [
        f"# {doc['title']} — 视频画面 OCR",
        "",
        f"- 来源：{doc['url']}",
        f"- 抽帧间隔：{interval} 秒",
        f"- 抽帧模式：{extraction_mode}",
        "- 用途：核验无旁白内容及英文项目名；原视频仅作临时文件",
        "",
    ]
    for record in records:
        md_lines.append(f"## [{record['stamp']}]")
        md_lines.append("")
        md_lines.extend(
            f"- {item['text']}（置信度 {item['score']:.2f}）"
            for item in record["lines"]
        )
        md_lines.append("")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    now = utcnow()
    with connect() as con:
        old_chunk_ids = [
            row["id"]
            for row in con.execute(
                "SELECT id FROM chunks WHERE document_id=? AND position>=100000",
                (doc["id"],),
            )
        ]
        if old_chunk_ids:
            con.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id=?",
                ((chunk_id,) for chunk_id in old_chunk_ids),
            )
            con.execute(
                "DELETE FROM chunks WHERE document_id=? AND position>=100000",
                (doc["id"],),
            )
        ocr_text_parts = []
        for position, record in enumerate(records, start=100000):
            text = "；".join(item["text"] for item in record["lines"])
            ocr_text_parts.append(text)
            cur = con.execute(
                """
                INSERT INTO chunks(document_id, position, text, start_sec, end_sec)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc["id"], position, text, record["time"], record["time"] + interval),
            )
            con.execute(
                """
                INSERT INTO chunks_fts(chunk_id, document_id, title, text)
                VALUES (?, ?, ?, ?)
                """,
                (cur.lastrowid, doc["id"], doc["title"], text),
            )
        metadata = json.loads(doc["metadata_json"] or "{}")
        metadata["ocr_path"] = str(md_path)
        metadata["ocr_interval_seconds"] = interval
        base_text = doc["text"].split("\n\n[视频画面 OCR]\n", 1)[0]
        merged = base_text
        if ocr_text_parts:
            merged += "\n\n[视频画面 OCR]\n" + "\n".join(ocr_text_parts)
        con.execute(
            """
            UPDATE documents
            SET text=?, metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (merged, json.dumps(metadata, ensure_ascii=False), now, doc["id"]),
        )

    verified = _store_visual_evidence(source_id, records)
    if not keep_video:
        video.unlink(missing_ok=True)
    return {
        "id": source_id,
        "frames_with_text": len(records),
        "kept_evidence_frames": len(list(frame_dir.glob("*.jpg"))),
        "sampled_frames": len(frame_times),
        "extraction_mode": extraction_mode,
        **verified,
        "temporary_video_removed": not video.exists(),
        "ocr_markdown": str(md_path),
    }
