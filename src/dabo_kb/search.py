from __future__ import annotations

import re

from .db import connect


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w\u3400-\u9fff.-]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{term}"' for term in terms[:12])


def search(query: str, limit: int = 8) -> list[dict]:
    expression = _fts_query(query)
    if not expression:
        return []
    with connect() as con:
        rows = con.execute(
            """
            SELECT d.source_id, d.title, d.url, c.start_sec, c.end_sec, c.text,
                   bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.id=chunks_fts.chunk_id
            JOIN documents d ON d.id=c.document_id
            WHERE chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
        if not rows:
            raw_terms = [
                term
                for term in re.split(r"\s+", query.strip())
                if len(term) >= 2
            ][:8]
            if raw_terms:
                where = " OR ".join("(c.text LIKE ? OR d.title LIKE ?)" for _ in raw_terms)
                params: list[object] = []
                for term in raw_terms:
                    pattern = f"%{term}%"
                    params.extend((pattern, pattern))
                params.append(limit)
                rows = con.execute(
                    f"""
                    SELECT d.source_id, d.title, d.url, c.start_sec, c.end_sec, c.text,
                           0.0 AS score
                    FROM chunks c
                    JOIN documents d ON d.id=c.document_id
                    WHERE {where}
                    ORDER BY c.document_id, c.position
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
    return [
        {
            "id": row["source_id"],
            "title": row["title"],
            "url": row["url"],
            "start": row["start_sec"],
            "end": row["end_sec"],
            "text": row["text"],
            "score": row["score"],
        }
        for row in rows
    ]


def document(source_id: str) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM documents WHERE source_id=? ORDER BY id LIMIT 1", (source_id,)
        ).fetchone()
    return dict(row) if row else None
