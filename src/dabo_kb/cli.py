from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import SETTINGS
from .db import connect
from .graph import build_graph, related
from .ingest import ingest_favorites
from .routing import route_title
from .search import document, search
from .transcribe import transcribe_document, transcribe_pending


def emit(value: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def status() -> dict:
    with connect() as con:
        counts = {
            row["status"]: row["count"]
            for row in con.execute(
                "SELECT status, COUNT(*) AS count FROM documents GROUP BY status"
            )
        }
        total = con.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        ai = con.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE is_ai=1"
        ).fetchone()["n"]
        chunks = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        verifications = {
            row["status"]: row["count"]
            for row in con.execute(
                "SELECT status, COUNT(*) AS count FROM name_verifications GROUP BY status"
            )
        }
        ai_rows = con.execute(
            "SELECT source_id, status, metadata_json FROM documents WHERE is_ai=1"
        ).fetchall()
    audio_ids = {path.stem for path in SETTINGS.media.glob("*.m4a")}
    ready = sum(
        row["status"] != "transcribed" and row["source_id"] in audio_ids
        for row in ai_rows
    )
    transcribed_ai = sum(row["status"] == "transcribed" for row in ai_rows)
    return {
        "documents": total,
        "ai_documents": ai,
        "ai_transcribed": transcribed_ai,
        "ai_remaining": ai - transcribed_ai,
        "chunks": chunks,
        "statuses": counts,
        "audio_downloaded": len(audio_ids),
        "ready_to_transcribe": ready,
        "ocr_documents": len(list(SETTINGS.transcripts.glob("*.ocr.json"))),
        "name_verifications": verifications,
    }


def pending(limit: int = 50) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT source_id AS id, title, url, status
            FROM documents
            WHERE is_ai=1 AND status!='transcribed'
            ORDER BY id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["has_audio"] = (SETTINGS.media / f"{item['id']}.m4a").exists()
        result.append(item)
    return result


def ocr_needed(limit: int = 50) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT source_id AS id, title, url, text, metadata_json
            FROM documents
            WHERE is_ai=1 AND status='transcribed'
            ORDER BY source_id
            """
        ).fetchall()
    result = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        corpus = f"{row['title']}\n{row['text']}".casefold()
        if metadata.get("ocr_path"):
            continue
        asr = metadata.get("asr") or {}
        if (
            asr.get("needs_visual_review")
            or "github" in corpus
            or "开源" in corpus
        ):
            result.append(
                {"id": row["id"], "title": row["title"], "url": row["url"]}
            )
        if len(result) >= limit:
            break
    return result


def verified_names(source_id: str, status_filter: str | None = None) -> list[dict]:
    sql = """
        SELECT
          nv.surface_form,
          nv.canonical_name,
          nv.kind,
          nv.evidence_type,
          nv.evidence_ref,
          nv.status
        FROM name_verifications nv
        JOIN documents d ON d.id=nv.document_id
        WHERE d.source_type='douyin' AND d.source_id=?
    """
    params: list[object] = [source_id]
    if status_filter:
        sql += " AND nv.status=?"
        params.append(status_filter)
    sql += " ORDER BY nv.kind, nv.canonical_name, nv.surface_form"
    with connect() as con:
        return [
            dict(row)
            for row in con.execute(sql, tuple(params)).fetchall()
        ]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="dabo-kb")
    sub = root.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="导入抖音收藏清单")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--ai-only", action="store_true")
    ingest.add_argument(
        "--replace-source",
        action="store_true",
        help="以完整快照为准，移除不在清单中的旧抖音记录",
    )

    transcribe = sub.add_parser("transcribe", help="转写一个音频")
    transcribe.add_argument("id")
    transcribe.add_argument("--model", default="small")

    transcribe_all = sub.add_parser(
        "transcribe-all", help="批量转写尚未处理的 AI 音频"
    )
    transcribe_all.add_argument("--model", default="small")

    queue = sub.add_parser(
        "build-queue",
        help="建立不含签名地址、可断点续跑的 AI 收藏处理清单",
    )
    queue.add_argument("--limit", type=int)
    queue.add_argument("--include-transcribed", action="store_true")

    capture = sub.add_parser(
        "import-capture",
        help="解析收藏列表接口响应并保存临时媒体候选地址",
    )
    capture.add_argument("path", type=Path)
    capture.add_argument("--include-transcribed", action="store_true")
    capture.add_argument(
        "--replace",
        action="store_true",
        help="清空旧的临时签名地址，而不是按作品 ID 合并",
    )

    public = sub.add_parser(
        "fetch-public",
        help="从公开分享页批量解析媒体地址，不读取浏览器 Cookie",
    )
    public.add_argument("id", nargs="?")
    public.add_argument("--limit", type=int, default=20)
    public.add_argument("--workers", type=int, default=4)
    public.add_argument("--replace", action="store_true")

    download = sub.add_parser(
        "download-media",
        help="音频优先下载；必要时下载视频并自动抽取音轨",
    )
    download.add_argument("id", nargs="?")
    download.add_argument("--limit", type=int, default=20)

    video = sub.add_parser(
        "download-video",
        help="为英文项目名画面核验临时下载原视频",
    )
    video.add_argument("id")

    route = sub.add_parser(
        "route",
        help="预览收藏会进入 OpenVINO base 还是 small 通道",
    )
    route.add_argument("id")

    smart = sub.add_parser(
        "transcribe-smart",
        help="使用 OpenVINO base/small 双通道批量转写",
    )
    smart.add_argument("id", nargs="?")
    smart.add_argument("--limit", type=int)
    smart.add_argument("--force", action="store_true")
    smart.add_argument("--parallel-workers", type=int, default=2)

    process = sub.add_parser(
        "process-batch",
        help="一键执行公开地址解析、音轨保存和双通道转写",
    )
    process.add_argument("--limit", type=int, default=10)
    process.add_argument("--workers", type=int, default=4)
    process.add_argument("--parallel-workers", type=int, default=2)

    process_all = sub.add_parser(
        "process-all",
        help="按短批次断点续跑全部剩余收藏，完成后更新索引和图谱",
    )
    process_all.add_argument("--batch-size", type=int, default=10)
    process_all.add_argument("--workers", type=int, default=4)
    process_all.add_argument("--parallel-workers", type=int, default=2)
    process_all.add_argument("--max-stalled-batches", type=int, default=3)
    process_limit = process_all.add_mutually_exclusive_group()
    process_limit.add_argument("--max-batches", type=int)
    process_limit.add_argument(
        "--count",
        type=int,
        help="仅新增处理指定数量，最后一批自动缩小",
    )
    process_all.add_argument("--skip-artifacts", action="store_true")

    ocr = sub.add_parser("ocr-video", help="抽取视频关键帧并做本地中英文 OCR")
    ocr.add_argument("id")
    ocr.add_argument("--interval", type=int, default=10)
    ocr.add_argument("--keep-video", action="store_true")
    ocr.add_argument(
        "--targeted",
        action="store_true",
        help="按逐字稿中的榜单名次时间点抽取少量关键帧",
    )

    reverify = sub.add_parser(
        "reverify-ocr", help="用严格规则重新核验已有 OCR 中的 GitHub 仓库名"
    )
    reverify.add_argument("id")

    query = sub.add_parser("search", help="检索知识库")
    query.add_argument("query")
    query.add_argument("--limit", type=int, default=8)
    query.add_argument("--semantic", action="store_true")

    doc = sub.add_parser("get", help="读取一个文档")
    doc.add_argument("id")

    rel = sub.add_parser("related", help="查询知识图谱中的相关实体")
    rel.add_argument("name")
    rel.add_argument("--limit", type=int, default=10)

    index = sub.add_parser("index", help="建立语义向量索引")
    index.add_argument(
        "--rebuild",
        action="store_true",
        help="忽略已有向量并全量重建索引",
    )
    sub.add_parser("graph", help="重建知识图谱")
    sub.add_parser("status", help="显示处理进度")
    pending_cmd = sub.add_parser("pending", help="列出尚未转写的 AI 收藏")
    pending_cmd.add_argument("--limit", type=int, default=50)
    ocr_needed_cmd = sub.add_parser(
        "ocr-needed", help="列出应进一步做画面核验的 GitHub/开源视频"
    )
    ocr_needed_cmd.add_argument("--limit", type=int, default=50)
    names = sub.add_parser(
        "names",
        help="列出一个作品中经过证据核验的项目或仓库名",
    )
    names.add_argument("id")
    names.add_argument(
        "--status",
        choices=["pending", "verified", "rejected"],
    )
    review = sub.add_parser(
        "review-needed",
        help="自动为待核验作品临时下载视频、定向 OCR 并删除视频",
    )
    review.add_argument("id", nargs="?")
    review.add_argument("--limit", type=int, default=1)
    return root


def main() -> None:
    SETTINGS.ensure()
    args = parser().parse_args()
    if args.command == "ingest":
        emit(ingest_favorites(args.path, args.ai_only, args.replace_source))
    elif args.command == "transcribe":
        emit(transcribe_document(args.id, args.model))
    elif args.command == "transcribe-all":
        emit(transcribe_pending(args.model))
    elif args.command == "build-queue":
        from .acquire import build_processing_queue

        emit(
            build_processing_queue(
                limit=args.limit,
                include_transcribed=args.include_transcribed,
            )
        )
    elif args.command == "import-capture":
        from .acquire import import_capture

        emit(
            import_capture(
                args.path,
                pending_only=not args.include_transcribed,
                replace=args.replace,
            )
        )
    elif args.command == "fetch-public":
        from .acquire import fetch_public_candidates

        emit(
            fetch_public_candidates(
                source_id=args.id,
                limit=args.limit,
                workers=args.workers,
                replace=args.replace,
            )
        )
    elif args.command == "download-media":
        from .acquire import download_candidate, download_pending

        if args.id:
            emit(download_candidate(args.id))
        else:
            emit(download_pending(args.limit))
    elif args.command == "download-video":
        from .acquire import download_video_evidence

        emit(download_video_evidence(args.id))
    elif args.command == "route":
        with connect() as con:
            row = con.execute(
                """
                SELECT source_id, title
                FROM documents
                WHERE source_type='douyin' AND source_id=?
                """,
                (args.id,),
            ).fetchone()
        if not row:
            raise ValueError(f"未找到文档 {args.id}")
        emit({"id": args.id, "title": row["title"], **route_title(row["title"])})
    elif args.command == "transcribe-smart":
        from .openvino_asr import run_in_openvino_environment

        emit(
            run_in_openvino_environment(
                source_id=args.id,
                limit=args.limit,
                force=args.force,
                parallel_workers=args.parallel_workers,
            )
        )
    elif args.command == "process-batch":
        from .pipeline import process_batch

        emit(process_batch(limit=args.limit, workers=args.workers, parallel_workers=args.parallel_workers))
    elif args.command == "process-all":
        from .pipeline import process_all

        emit(
            process_all(
                batch_size=args.batch_size,
                workers=args.workers,
                max_stalled_batches=args.max_stalled_batches,
                max_batches=args.max_batches,
                count=args.count,
                update_artifacts=not args.skip_artifacts,
                parallel_workers=args.parallel_workers,
            )
        )
    elif args.command == "ocr-video":
        from .ocr import ocr_video

        emit(
            ocr_video(
                args.id,
                args.interval,
                keep_video=args.keep_video,
                targeted=args.targeted,
            )
        )
    elif args.command == "reverify-ocr":
        from .ocr import reverify_ocr

        emit(reverify_ocr(args.id))
    elif args.command == "search":
        if args.semantic:
            from .vector import semantic_search

            emit(semantic_search(args.query, args.limit))
        else:
            emit(search(args.query, args.limit))
    elif args.command == "get":
        emit(document(args.id))
    elif args.command == "related":
        emit(related(args.name, args.limit))
    elif args.command == "index":
        from .vector import build_vector_index

        emit(build_vector_index(force=args.rebuild))
    elif args.command == "graph":
        emit(build_graph())
    elif args.command == "status":
        emit(status())
    elif args.command == "pending":
        emit(pending(args.limit))
    elif args.command == "ocr-needed":
        emit(ocr_needed(args.limit))
    elif args.command == "names":
        emit(verified_names(args.id, args.status))
    elif args.command == "review-needed":
        from .review import review_document, review_pending

        if args.id:
            emit(review_document(args.id))
        else:
            emit(review_pending(args.limit))


if __name__ == "__main__":
    main()
