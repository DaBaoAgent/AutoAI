from __future__ import annotations

import sqlite3
import sys
import types
from contextlib import contextmanager

import numpy as np

from dabo_kb import vector
from dabo_kb.db import SCHEMA


class FakeTextEmbedding:
    embedded: list[str] = []

    def __init__(self, model_name: str):
        self.model_name = model_name

    def embed(self, texts: list[str], batch_size: int):
        self.embedded.extend(texts)
        for position, _ in enumerate(texts):
            yield np.asarray([position + 1.0, 2.0], dtype=np.float32)


def test_build_vector_index_only_embeds_missing_chunks(tmp_path, monkeypatch):
    path = tmp_path / "knowledge.db"

    @contextmanager
    def temp_connect():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    monkeypatch.setattr(vector, "connect", temp_connect)
    monkeypatch.setitem(
        sys.modules,
        "fastembed",
        types.SimpleNamespace(TextEmbedding=FakeTextEmbedding),
    )
    FakeTextEmbedding.embedded = []

    with temp_connect() as con:
        cur = con.execute(
            """
            INSERT INTO documents(
              source_type, source_id, title, text, is_ai, status,
              metadata_json, created_at, updated_at
            ) VALUES ('douyin', '1', 'test', '', 1, 'transcribed', '{}', 'now', 'now')
            """
        )
        document_id = cur.lastrowid
        con.executemany(
            "INSERT INTO chunks(document_id, position, text) VALUES (?, ?, ?)",
            [(document_id, 0, "existing"), (document_id, 1, "new")],
        )
        first_id = con.execute(
            "SELECT id FROM chunks WHERE position=0"
        ).fetchone()["id"]
        con.execute(
            "INSERT INTO vectors(chunk_id, model, dim, vector) VALUES (?, ?, 2, ?)",
            (
                first_id,
                vector.EMBED_MODEL,
                np.asarray([1.0, 2.0], dtype=np.float32).tobytes(),
            ),
        )

    result = vector.build_vector_index()

    assert result["existing"] == 1
    assert result["added"] == 1
    assert result["chunks"] == 2
    assert result["mode"] == "incremental"
    assert FakeTextEmbedding.embedded == ["new"]
    with temp_connect() as con:
        assert con.execute("SELECT COUNT(*) AS n FROM vectors").fetchone()["n"] == 2
