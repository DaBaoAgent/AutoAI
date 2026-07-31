from __future__ import annotations

import json

from .config import SETTINGS, WHISPER_MODEL
from .db import connect, replace_chunks, utcnow
from .routing import transcript_needs_visual_review


def _stamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def persist_transcript(
    source_id: str,
    rows: list[dict],
    *,
    language: str = "zh",
    language_probability: float | None = None,
    engine: str,
    model_name: str,
    lane: str | None = None,
    infer_sec: float | None = None,
) -> dict:
    with connect() as con:
        doc = con.execute(
            "SELECT * FROM documents WHERE source_type='douyin' AND source_id=?",
            (source_id,),
        ).fetchone()
    if not doc:
        raise ValueError(f"未找到文档 {source_id}")

    audio = SETTINGS.media / f"{source_id}.m4a"
    if not audio.exists():
        raise FileNotFoundError(f"缺少音频：{audio}")

    text = "\n".join(row["text"] for row in rows)
    needs_visual_review = transcript_needs_visual_review(text, doc["title"])
    asr_metadata = {
        "engine": engine,
        "model": model_name,
        "lane": lane,
        "infer_sec": infer_sec,
        "needs_visual_review": needs_visual_review,
    }
    json_path = SETTINGS.transcripts / f"{source_id}.json"
    md_path = SETTINGS.transcripts / f"{source_id}.md"
    json_path.write_text(
        json.dumps(
            {
                "id": source_id,
                "title": doc["title"],
                "url": doc["url"],
                "language": language,
                "language_probability": language_probability,
                "asr": asr_metadata,
                "segments": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [
        f"# {doc['title']}",
        "",
        f"- 来源：{doc['url']}",
        f"- 音频：`{audio.name}`",
        f"- 语言：{language}",
        f"- 转写：{engine} / {model_name}",
        f"- 通道：{lane or 'manual'}",
        f"- 画面核验：{'需要' if needs_visual_review else '暂不需要'}",
        "",
        "## 逐字稿",
        "",
    ]
    lines.extend(f"[{_stamp(row['start'])}] {row['text']}" for row in rows)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    metadata = json.loads(doc["metadata_json"] or "{}")
    metadata["asr"] = asr_metadata
    with connect() as con:
        con.execute(
            """
            UPDATE documents
            SET text=?, audio_path=?, transcript_path=?, status='transcribed',
                metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (
                text,
                str(audio),
                str(md_path),
                json.dumps(metadata, ensure_ascii=False),
                utcnow(),
                doc["id"],
            ),
        )
    replace_chunks(int(doc["id"]), doc["title"], rows)
    return {
        "id": source_id,
        "segments": len(rows),
        "characters": len(text),
        "transcript": str(md_path),
        "needs_visual_review": needs_visual_review,
        "engine": engine,
        "model": model_name,
        "lane": lane,
    }


def _transcribe_with_model(source_id: str, model, model_name: str) -> dict:
    audio = SETTINGS.media / f"{source_id}.m4a"
    segments, info = model.transcribe(
        str(audio),
        language="zh",
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=True,
    )
    rows = [
        {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
        for s in segments
        if s.text.strip()
    ]
    return persist_transcript(
        source_id,
        rows,
        language=info.language,
        language_probability=info.language_probability,
        engine="faster-whisper",
        model_name=model_name,
    )


def transcribe_document(source_id: str, model_name: str = WHISPER_MODEL) -> dict:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4)
    return _transcribe_with_model(source_id, model, model_name)


def transcribe_pending(model_name: str = WHISPER_MODEL) -> dict:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4)
    with connect() as con:
        ids = [
            row["source_id"]
            for row in con.execute(
                """
                SELECT source_id
                FROM documents
                WHERE source_type='douyin' AND is_ai=1 AND status!='transcribed'
                ORDER BY id
                """
            )
            if (SETTINGS.media / f"{row['source_id']}.m4a").exists()
        ]
    results = []
    for index, source_id in enumerate(ids, start=1):
        result = _transcribe_with_model(source_id, model, model_name)
        result["progress"] = f"{index}/{len(ids)}"
        print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
    return {
        "processed": len(results),
        "segments": sum(item["segments"] for item in results),
        "characters": sum(item["characters"] for item in results),
    }
