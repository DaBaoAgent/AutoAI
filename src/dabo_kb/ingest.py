from __future__ import annotations

import json
import re
from pathlib import Path

from .db import connect, utcnow


AI_TERMS = re.compile(
    r"(?i)(\bai\b|人工智能|大模型|llm|agent|智能体|skill|codex|claude|"
    r"chatgpt|openai|openclaw|github|开源项目|vibe.?coding|comfyui|seed3d|"
    r"本地部署|token|mcp|rag|提示词|工作流|自动化|模型|aigc)"
)


def looks_ai(text: str) -> bool:
    return bool(AI_TERMS.search(text))


def ingest_favorites(
    path: Path, ai_only: bool = False, replace_source: bool = False
) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    account = payload.get("account", {})
    imported = 0
    ai_count = 0
    rows = []
    now = utcnow()
    for item in payload.get("items", []):
        title = item.get("title", "").strip()
        is_ai = looks_ai(title)
        if ai_only and not is_ai:
            continue
        rows.append(
            (
                "douyin",
                str(item["id"]),
                title,
                item.get("url"),
                int(is_ai),
                json.dumps(
                    {
                        "account": account,
                        "collected_at": payload.get("collected_at"),
                    },
                    ensure_ascii=False,
                ),
                now,
                now,
            )
        )
        imported += 1
        ai_count += int(is_ai)
    with connect() as con:
        con.executemany(
            """
            INSERT INTO documents(
              source_type, source_id, title, url, is_ai,
              metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, source_id) DO UPDATE SET
              title=excluded.title,
              url=excluded.url,
              is_ai=MAX(excluded.is_ai, documents.is_ai),
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            rows,
        )
        removed = 0
        if replace_source:
            con.execute("CREATE TEMP TABLE sync_ids(source_id TEXT PRIMARY KEY)")
            con.executemany(
                "INSERT INTO sync_ids(source_id) VALUES (?)",
                ((str(item["id"]),) for item in payload.get("items", [])),
            )
            removed = con.execute(
                """
                DELETE FROM documents
                WHERE source_type='douyin'
                  AND source_id NOT IN (SELECT source_id FROM sync_ids)
                """
            ).rowcount
    return {
        "imported": imported,
        "ai": ai_count,
        "source_count": payload.get("count", 0),
        "removed_stale": removed,
    }
