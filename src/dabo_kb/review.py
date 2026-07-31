from __future__ import annotations

import json

from .acquire import download_video_evidence, fetch_public_candidates
from .db import connect
from .ocr import ocr_video


def review_candidates(limit: int = 20) -> list[dict[str, str]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT source_id, title, text, metadata_json
            FROM documents
            WHERE source_type='douyin' AND is_ai=1 AND status='transcribed'
            ORDER BY id
            """
        ).fetchall()
    result = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        if metadata.get("ocr_path"):
            continue
        corpus = f"{row['title']}\n{row['text']}".casefold()
        asr = metadata.get("asr") or {}
        if not (
            asr.get("needs_visual_review")
            or "github" in corpus
            or "开源" in corpus
        ):
            continue
        result.append({"id": row["source_id"], "title": row["title"]})
        if len(result) >= limit:
            break
    return result


def review_document(source_id: str) -> dict[str, object]:
    fetched = fetch_public_candidates(source_id=source_id, workers=1)
    if fetched["resolved"] != 1:
        raise RuntimeError(
            f"{source_id} 无法从公开分享页获取证据视频：{fetched['failures']}"
        )
    video = download_video_evidence(source_id)
    ocr = ocr_video(source_id, interval=10, targeted=True)
    return {"id": source_id, "fetch": fetched, "video": video, "ocr": ocr}


def review_pending(limit: int = 1) -> dict[str, object]:
    items = review_candidates(limit)
    results = []
    failures = []
    for item in items:
        try:
            results.append(review_document(item["id"]))
        except Exception as exc:
            failures.append({"id": item["id"], "error": str(exc)})
    return {
        "requested": len(items),
        "reviewed": len(results),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }
