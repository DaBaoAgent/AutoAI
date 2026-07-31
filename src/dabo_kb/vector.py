from __future__ import annotations

import numpy as np

from .config import EMBED_MODEL, SETTINGS
from .db import connect


def build_vector_index(*, force: bool = False, batch_size: int = 64) -> dict:
    from fastembed import TextEmbedding

    SETTINGS.ensure()
    with connect() as con:
        if force:
            con.execute("DELETE FROM vectors")
        existing = con.execute(
            """
            SELECT COUNT(*) AS n
            FROM vectors v
            JOIN chunks c ON c.id=v.chunk_id
            JOIN documents d ON d.id=c.document_id
            WHERE d.is_ai=1 AND v.model=?
            """,
            (EMBED_MODEL,),
        ).fetchone()["n"]
        rows = con.execute(
            """
            SELECT c.id, c.document_id, c.position, c.text, c.start_sec, c.end_sec,
                   d.source_id, d.title, d.url
            FROM chunks c JOIN documents d ON d.id=c.document_id
            LEFT JOIN vectors v ON v.chunk_id=c.id AND v.model=?
            WHERE d.is_ai=1
              AND v.chunk_id IS NULL
            ORDER BY c.id
            """,
            (EMBED_MODEL,),
        ).fetchall()
    added = 0
    if rows:
        model = TextEmbedding(model_name=EMBED_MODEL)
        for offset in range(0, len(rows), batch_size):
            batch = rows[offset : offset + batch_size]
            embeddings = model.embed(
                [row["text"] for row in batch],
                batch_size=batch_size,
            )
            records = []
            for row, embedding in zip(batch, embeddings, strict=True):
                vector = np.asarray(embedding, dtype=np.float32)
                records.append(
                    (
                        row["id"],
                        EMBED_MODEL,
                        int(vector.size),
                        vector.tobytes(),
                    )
                )
            with connect() as con:
                con.executemany(
                    """
                    INSERT INTO vectors(chunk_id, model, dim, vector)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                      model=excluded.model,
                      dim=excluded.dim,
                      vector=excluded.vector
                    """,
                    records,
                )
            added += len(records)
    return {
        "chunks": existing + added,
        "existing": existing,
        "added": added,
        "model": EMBED_MODEL,
        "path": str(SETTINGS.db),
        "mode": "rebuild" if force else "incremental",
    }


def semantic_search(query: str, limit: int = 8) -> list[dict]:
    from fastembed import TextEmbedding

    with connect() as con:
        rows = con.execute(
            """
            SELECT v.vector, v.dim, c.text, c.start_sec, c.end_sec,
                   d.source_id, d.title, d.url
            FROM vectors v
            JOIN chunks c ON c.id=v.chunk_id
            JOIN documents d ON d.id=c.document_id
            WHERE v.model=?
            """,
            (EMBED_MODEL,),
        ).fetchall()
    if not rows:
        return []
    model = TextEmbedding(model_name=EMBED_MODEL)
    query_vector = np.asarray(next(model.query_embed(query)), dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vector)) or 1.0
    scored: list[tuple[float, object]] = []
    for row in rows:
        vector = np.frombuffer(row["vector"], dtype=np.float32, count=row["dim"])
        denom = (float(np.linalg.norm(vector)) or 1.0) * query_norm
        score = float(np.dot(vector, query_vector) / denom)
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "id": row["source_id"],
            "title": row["title"],
            "url": row["url"],
            "start": row["start_sec"],
            "end": row["end_sec"],
            "text": row["text"],
            "score": score,
        }
        for score, row in scored[:limit]
    ]
