from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import SETTINGS
from .db import connect
from .routing import route_title


DOUYIN_ID = re.compile(r"^\d{18,20}$")
QUEUE_NAME = "douyin_processing_queue.json"
CANDIDATES_NAME = "douyin_media_candidates.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Mobile Safari/537.36"
)
ROUTER_DATA_MARKER = "window._ROUTER_DATA = "


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def processing_queue_path() -> Path:
    return SETTINGS.sources / QUEUE_NAME


def candidates_path() -> Path:
    return SETTINGS.tmp / CANDIDATES_NAME


def _update_queue_state(source_id: str, state: str) -> None:
    path = processing_queue_path()
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for item in payload.get("items", []):
        if item.get("id") == source_id and item.get("state") != state:
            item["state"] = state
            changed = True
            break
    if changed:
        payload["updated_at"] = _now()
        _atomic_json(path, payload)


def build_processing_queue(
    *,
    limit: int | None = None,
    include_transcribed: bool = False,
) -> dict[str, object]:
    where = "source_type='douyin' AND is_ai=1"
    if not include_transcribed:
        where += " AND status!='transcribed'"
    sql = (
        "SELECT source_id, title, url, status FROM documents "
        f"WHERE {where} ORDER BY id"
    )
    params: tuple[object, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    with connect() as con:
        rows = con.execute(sql, params).fetchall()

    items = []
    for row in rows:
        source_id = row["source_id"]
        audio = SETTINGS.media / f"{source_id}.m4a"
        transcript = SETTINGS.transcripts / f"{source_id}.json"
        route = route_title(row["title"])
        state = "transcribed" if transcript.exists() else (
            "audio_ready" if audio.exists() else "awaiting_media"
        )
        items.append(
            {
                "id": source_id,
                "title": row["title"],
                "url": row["url"],
                "lane": route["lane"],
                "needs_name_review": route["needs_name_review"],
                "state": state,
            }
        )
    payload = {
        "schema_version": 1,
        "generated_at": _now(),
        "signed_urls_included": False,
        "count": len(items),
        "items": items,
    }
    _atomic_json(processing_queue_path(), payload)
    return {
        "path": str(processing_queue_path()),
        "count": len(items),
        "base": sum(item["lane"] == "base" for item in items),
        "small": sum(item["lane"] == "small" for item in items),
        "awaiting_media": sum(
            item["state"] == "awaiting_media" for item in items
        ),
        "audio_ready": sum(item["state"] == "audio_ready" for item in items),
    }


def _url_list(value: object) -> list[str]:
    if isinstance(value, dict):
        value = value.get("url_list") or value.get("urlList") or []
    if not isinstance(value, list):
        return []
    return [
        url
        for url in value
        if isinstance(url, str) and url.startswith(("http://", "https://"))
    ]


def _nested(obj: dict[str, Any], *keys: str) -> object:
    value: object = obj
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


@dataclass
class MediaCandidate:
    id: str
    title: str
    duration_ms: int | None
    audio_urls: list[str]
    video_urls: list[str]


def _candidate(obj: dict[str, Any]) -> MediaCandidate | None:
    raw_id = obj.get("aweme_id") or obj.get("awemeId")
    source_id = str(raw_id or "")
    if not DOUYIN_ID.fullmatch(source_id):
        return None
    if not isinstance(obj.get("video"), dict) and not isinstance(
        obj.get("music"), dict
    ):
        return None
    title = str(obj.get("desc") or obj.get("title") or "").strip()
    duration = obj.get("duration")
    if not isinstance(duration, int):
        duration = _nested(obj, "video", "duration")
    duration_ms = duration if isinstance(duration, int) else None

    audio_urls = _url_list(_nested(obj, "music", "play_url"))
    if not audio_urls:
        audio_urls = _url_list(_nested(obj, "music", "playUrl"))
    video_urls: list[str] = []
    for path in (
        ("video", "play_addr"),
        ("video", "playAddr"),
        ("video", "download_addr"),
        ("video", "downloadAddr"),
    ):
        for url in _url_list(_nested(obj, *path)):
            if url not in video_urls:
                video_urls.append(url)
    return MediaCandidate(
        id=source_id,
        title=title,
        duration_ms=duration_ms,
        audio_urls=list(dict.fromkeys(audio_urls)),
        video_urls=video_urls,
    )


def extract_media_candidates(payload: object) -> list[MediaCandidate]:
    """Extract aweme media candidates from a captured Douyin JSON response."""
    found: dict[str, MediaCandidate] = {}
    stack = [payload]
    seen: set[int] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(value, dict):
            item = _candidate(value)
            if item:
                previous = found.get(item.id)
                if previous:
                    previous.audio_urls = list(
                        dict.fromkeys(previous.audio_urls + item.audio_urls)
                    )
                    previous.video_urls = list(
                        dict.fromkeys(previous.video_urls + item.video_urls)
                    )
                    if not previous.title:
                        previous.title = item.title
                    if previous.duration_ms is None:
                        previous.duration_ms = item.duration_ms
                else:
                    found[item.id] = item
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return list(found.values())


def import_capture(
    path: Path,
    *,
    pending_only: bool = True,
    replace: bool = False,
) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    extracted = extract_media_candidates(payload)
    with connect() as con:
        rows = con.execute(
            """
            SELECT source_id, status
            FROM documents
            WHERE source_type='douyin' AND is_ai=1
            """
        ).fetchall()
    allowed = {
        row["source_id"]
        for row in rows
        if not pending_only or row["status"] != "transcribed"
    }
    accepted = [item for item in extracted if item.id in allowed]
    merged = _merge_candidate_rows(accepted, replace=replace)
    candidate_payload = {
        "schema_version": 1,
        "captured_at": _now(),
        "ephemeral": True,
        "contains_signed_urls": True,
        "items": list(merged.values()),
    }
    _atomic_json(candidates_path(), candidate_payload)
    return {
        "path": str(candidates_path()),
        "extracted": len(extracted),
        "accepted_pending_ai": len(accepted),
        "temporary_total": len(merged),
        "with_audio_candidate": sum(bool(item.audio_urls) for item in accepted),
        "with_video_candidate": sum(bool(item.video_urls) for item in accepted),
    }


def _merge_candidate_rows(
    accepted: Iterable[MediaCandidate],
    *,
    replace: bool,
) -> dict[str, dict[str, Any]]:
    accepted = list(accepted)
    merged: dict[str, dict[str, Any]] = {}
    if candidates_path().exists() and not replace:
        previous = json.loads(candidates_path().read_text(encoding="utf-8"))
        merged = {
            str(item["id"]): item
            for item in previous.get("items", [])
            if DOUYIN_ID.fullmatch(str(item.get("id") or ""))
        }
    captured_at = _now()
    for item in accepted:
        row = asdict(item)
        row["captured_at"] = captured_at
        merged[item.id] = row
        _update_queue_state(item.id, "media_candidate_ready")
    return merged


def _fetch_share_payload(source_id: str) -> object:
    url = f"https://www.iesdouyin.com/share/video/{source_id}/"
    completed = subprocess.run(
        [
            "curl.exe",
            "-L",
            "--fail",
            "--connect-timeout",
            "10",
            "--max-time",
            "40",
            "-sS",
            "-A",
            MOBILE_USER_AGENT,
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-800:] or "公开分享页请求失败")
    html = completed.stdout
    start = html.find(ROUTER_DATA_MARKER)
    if start < 0:
        raise RuntimeError("公开分享页中未找到作品数据")
    start += len(ROUTER_DATA_MARKER)
    end = html.find("</script>", start)
    if end < 0:
        raise RuntimeError("公开分享页作品数据不完整")
    raw = html[start:end].strip().removesuffix(";").strip()
    return json.loads(raw)


def fetch_public_candidates(
    *,
    source_id: str | None = None,
    limit: int = 20,
    workers: int = 4,
    replace: bool = False,
) -> dict[str, object]:
    """Resolve public Douyin share pages without browser credentials."""
    where = [
        "source_type='douyin'",
        "is_ai=1",
    ]
    params: list[object] = []
    if source_id:
        where.append("source_id=?")
        params.append(source_id)
    else:
        where.append("status!='transcribed'")
    sql = (
        "SELECT source_id FROM documents WHERE "
        + " AND ".join(where)
        + " ORDER BY id"
    )
    with connect() as con:
        eligible_ids = [
            row["source_id"]
            for row in con.execute(sql, tuple(params)).fetchall()
            if source_id
            or not (SETTINGS.media / f"{row['source_id']}.m4a").exists()
        ]
    ids = eligible_ids[: 1 if source_id else limit]

    accepted: list[MediaCandidate] = []
    failures: list[dict[str, str]] = []

    def fetch_one(item_id: str) -> MediaCandidate:
        payload = _fetch_share_payload(item_id)
        matches = [
            item
            for item in extract_media_candidates(payload)
            if item.id == item_id
        ]
        if not matches:
            raise RuntimeError("作品数据中没有可用的媒体地址")
        return matches[0]

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        futures = {executor.submit(fetch_one, item_id): item_id for item_id in ids}
        for future in as_completed(futures):
            item_id = futures[future]
            try:
                accepted.append(future.result())
            except Exception as exc:
                failures.append({"id": item_id, "error": str(exc)})

    merged = _merge_candidate_rows(accepted, replace=replace)
    captured_at = _now()
    payload = {
        "schema_version": 1,
        "captured_at": captured_at,
        "ephemeral": True,
        "contains_signed_urls": True,
        "source": "public_share_page",
        "items": list(merged.values()),
    }
    _atomic_json(candidates_path(), payload)
    return {
        "path": str(candidates_path()),
        "requested": len(ids),
        "resolved": len(accepted),
        "failed": len(failures),
        "temporary_total": len(merged),
        "with_audio_candidate": sum(bool(item.audio_urls) for item in accepted),
        "with_video_candidate": sum(bool(item.video_urls) for item in accepted),
        "failures": failures,
    }


def _probe(path: Path) -> dict[str, object]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1000:] or f"ffprobe failed: {path}")
    data = json.loads(completed.stdout)
    fmt = data.get("format", {})
    return {
        "duration_sec": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
    }


def _duration_matches(actual_sec: float, expected_ms: int | None) -> bool:
    if expected_ms is None or expected_ms <= 0:
        return actual_sec > 1
    expected_sec = expected_ms / 1000
    tolerance = max(3.0, expected_sec * 0.08)
    return abs(actual_sec - expected_sec) <= tolerance


def _download(url: str, destination: Path) -> None:
    completed = subprocess.run(
        [
            "curl.exe",
            "-L",
            "--fail",
            "--connect-timeout",
            "15",
            "--max-time",
            "300",
            "-A",
            USER_AGENT,
            "-e",
            "https://www.douyin.com/",
            "-H",
            "Range: bytes=0-",
            "-o",
            str(destination),
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:] or "media download failed")


def _convert_to_m4a(source: Path, audio: Path) -> None:
    copied = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "copy",
            str(audio),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if copied.returncode == 0:
        return
    encoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(audio),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if encoded.returncode != 0:
        raise RuntimeError(encoded.stderr[-1200:] or "M4A conversion failed")


def _candidate_items() -> list[dict[str, Any]]:
    path = candidates_path()
    if not path.exists():
        raise FileNotFoundError(
            f"缺少临时媒体候选清单：{path}；请先运行 import-capture"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("items", []))


def download_candidate(source_id: str) -> dict[str, object]:
    target = SETTINGS.media / f"{source_id}.m4a"
    if target.exists():
        info = _probe(target)
        return {"id": source_id, "state": "already_exists", **info}
    item = next(
        (entry for entry in _candidate_items() if entry.get("id") == source_id),
        None,
    )
    if not item:
        raise ValueError(f"临时媒体候选中没有作品 {source_id}")

    tmp_download = SETTINGS.tmp / f"{source_id}.download.media"
    tmp_audio = SETTINGS.tmp / f"{source_id}.download.m4a"
    tmp_video = SETTINGS.tmp / f"{source_id}.download.mp4"
    failures: list[str] = []
    for url in item.get("audio_urls", []):
        try:
            _download(url, tmp_download)
            downloaded_info = _probe(tmp_download)
            if not _duration_matches(
                float(downloaded_info["duration_sec"]),
                item.get("duration_ms"),
            ):
                raise RuntimeError("独立音频时长与作品不匹配")
            _convert_to_m4a(tmp_download, tmp_audio)
            info = _probe(tmp_audio)
            tmp_audio.replace(target)
            tmp_download.unlink(missing_ok=True)
            _update_queue_state(source_id, "audio_ready")
            return {
                "id": source_id,
                "state": "downloaded_audio",
                **info,
            }
        except Exception as exc:  # continue across expiring CDN alternatives
            failures.append(f"audio: {exc}")
            tmp_download.unlink(missing_ok=True)
            tmp_audio.unlink(missing_ok=True)

    for url in item.get("video_urls", []):
        try:
            _download(url, tmp_video)
            _convert_to_m4a(tmp_video, tmp_audio)
            info = _probe(tmp_audio)
            if not _duration_matches(
                float(info["duration_sec"]),
                item.get("duration_ms"),
            ):
                raise RuntimeError("视频音轨时长与作品不匹配")
            tmp_audio.replace(target)
            tmp_video.unlink(missing_ok=True)
            _update_queue_state(source_id, "audio_ready")
            return {
                "id": source_id,
                "state": "downloaded_from_video",
                **info,
            }
        except Exception as exc:
            failures.append(f"video: {exc}")
            tmp_download.unlink(missing_ok=True)
            tmp_audio.unlink(missing_ok=True)
            tmp_video.unlink(missing_ok=True)
    raise RuntimeError(
        f"{source_id} 所有临时地址均失败或过期："
        + " | ".join(failures[-4:])
    )


def download_video_evidence(source_id: str) -> dict[str, object]:
    target = SETTINGS.media / f"{source_id}-video.mp4"
    if target.exists():
        return {"id": source_id, "state": "already_exists", **_probe(target)}
    item = next(
        (entry for entry in _candidate_items() if entry.get("id") == source_id),
        None,
    )
    if not item:
        raise ValueError(f"临时媒体候选中没有作品 {source_id}")
    temporary = SETTINGS.tmp / f"{source_id}.evidence.mp4"
    failures: list[str] = []
    for url in item.get("video_urls", []):
        try:
            _download(url, temporary)
            info = _probe(temporary)
            if not _duration_matches(
                float(info["duration_sec"]),
                item.get("duration_ms"),
            ):
                raise RuntimeError("证据视频时长与作品不匹配")
            temporary.replace(target)
            return {
                "id": source_id,
                "state": "video_evidence_ready",
                "path": str(target),
                **info,
            }
        except Exception as exc:
            failures.append(str(exc))
            temporary.unlink(missing_ok=True)
    raise RuntimeError(
        f"{source_id} 证据视频下载失败：" + " | ".join(failures[-3:])
    )


def _download_one(item: dict[str, Any]) -> dict[str, object]:
    """Download media for a single item. Module-level for ThreadPoolExecutor."""
    source_id = str(item.get("id") or "")
    if not DOUYIN_ID.fullmatch(source_id):
        return {"id": source_id, "state": "skipped", "error": "invalid id"}
    if (SETTINGS.media / f"{source_id}.m4a").exists():
        return {"id": source_id, "state": "already_exists"}
    try:
        return download_candidate(source_id)
    except Exception as exc:
        return {"id": source_id, "state": "failed", "error": str(exc)}


def download_pending(limit: int = 20, parallel: int = 4) -> dict[str, object]:
    items = _candidate_items()
    targets = []
    for item in items:
        source_id = str(item.get("id") or "")
        if not DOUYIN_ID.fullmatch(source_id):
            continue
        if (SETTINGS.media / f"{source_id}.m4a").exists():
            continue
        targets.append(item)
        if len(targets) >= limit:
            break

    if parallel <= 1 or len(targets) <= 1:
        results = [_download_one(item) for item in targets]
    else:
        workers = min(parallel, len(targets), 8)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_download_one, item): item for item in targets}
            results = [future.result() for future in as_completed(futures)]

    return {
        "processed": len(results),
        "downloaded": sum(
            str(item["state"]).startswith("downloaded") for item in results
        ),
        "failed": sum(item["state"] == "failed" for item in results),
        "results": results,
    }
