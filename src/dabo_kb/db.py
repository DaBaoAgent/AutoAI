from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import SETTINGS


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  author TEXT,
  published_at TEXT,
  text TEXT NOT NULL DEFAULT '',
  audio_path TEXT,
  transcript_path TEXT,
  is_ai INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'discovered',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source_type, source_id)
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_sec REAL,
  end_sec REAL,
  UNIQUE(document_id, position)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED,
  document_id UNINDEXED,
  title,
  text,
  tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS vectors (
  chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE,
  kind TEXT NOT NULL,
  UNIQUE(name, kind)
);

CREATE TABLE IF NOT EXISTS document_entities (
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  weight REAL NOT NULL DEFAULT 1,
  PRIMARY KEY(document_id, entity_id)
);

CREATE TABLE IF NOT EXISTS relations (
  source_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  target_entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  relation TEXT NOT NULL,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  weight REAL NOT NULL DEFAULT 1,
  PRIMARY KEY(source_entity_id, target_entity_id, relation, document_id)
);

CREATE TABLE IF NOT EXISTS name_verifications (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  surface_form TEXT NOT NULL COLLATE NOCASE,
  canonical_name TEXT,
  kind TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  evidence_ref TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending', 'verified', 'rejected')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(document_id, surface_form, kind)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    SETTINGS.ensure()
    con = sqlite3.connect(path or SETTINGS.db)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def upsert_document(
    *,
    source_type: str,
    source_id: str,
    title: str,
    url: str | None = None,
    author: str | None = None,
    is_ai: bool = False,
    status: str = "discovered",
    metadata: dict | None = None,
) -> int:
    now = utcnow()
    with connect() as con:
        con.execute(
            """
            INSERT INTO documents(
              source_type, source_id, title, url, author, is_ai, status,
              metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, source_id) DO UPDATE SET
              title=excluded.title,
              url=excluded.url,
              author=COALESCE(excluded.author, documents.author),
              is_ai=MAX(excluded.is_ai, documents.is_ai),
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                source_type,
                source_id,
                title,
                url,
                author,
                int(is_ai),
                status,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = con.execute(
            "SELECT id FROM documents WHERE source_type=? AND source_id=?",
            (source_type, source_id),
        ).fetchone()
        return int(row["id"])


def replace_chunks(document_id: int, title: str, chunks: list[dict]) -> None:
    with connect() as con:
        con.execute("DELETE FROM chunks_fts WHERE document_id=?", (document_id,))
        con.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        for position, chunk in enumerate(chunks):
            cur = con.execute(
                """
                INSERT INTO chunks(document_id, position, text, start_sec, end_sec)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    position,
                    chunk["text"],
                    chunk.get("start"),
                    chunk.get("end"),
                ),
            )
            con.execute(
                "INSERT INTO chunks_fts(chunk_id, document_id, title, text) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, document_id, title, chunk["text"]),
            )
