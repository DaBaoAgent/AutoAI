from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from itertools import combinations

import networkx as nx

from .config import SETTINGS
from .db import connect


REPO = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.-]{1,}/[A-Za-z0-9][A-Za-z0-9_.-]{1,})\b"
)
GITHUB_URL = re.compile(
    r"https?://(?:www\.)?github\.com/"
    r"([A-Za-z0-9_.-]{2,})/([A-Za-z0-9_.-]{2,})",
    re.IGNORECASE,
)
URL = re.compile(r"https?://\S+", re.IGNORECASE)
BLOCKED_OWNERS = {
    "build",
    "claude",
    "commands",
    "drive",
    "folders",
    "gpt",
    "gpu",
    "http",
    "https",
    "my",
    "pan",
    "rules",
    "src",
    "t",
    "www",
}
HASHTAG = re.compile(r"#([\w\u3400-\u9fff.-]+)")
TERMS = re.compile(
    r"(?i)\b(Codex|Claude Code|ChatGPT|OpenAI|Agent|Skill|MCP|RAG|ComfyUI|"
    r"GitHub|Whisper|Qdrant|LightRAG|OpenClaw|Hermes)\b"
)


def _repository_candidates(text: str) -> set[str]:
    candidates = {
        f"{owner}/{repository}".rstrip(".,;:")
        for owner, repository in GITHUB_URL.findall(text)
    }
    without_urls = URL.sub(" ", text)
    for name in REPO.findall(without_urls):
        owner, repository = name.split("/", 1)
        owner_key = owner.casefold()
        if owner_key in BLOCKED_OWNERS:
            continue
        if owner_key.startswith(("gpt-", "claude-")):
            continue
        if "." in owner:
            continue
        if not any(char.isalpha() for char in repository):
            continue
        candidates.add(name)
    return candidates


def build_graph() -> dict:
    SETTINGS.ensure()
    graph = nx.Graph()
    with connect() as con:
        docs = con.execute(
            "SELECT id, source_id, title, url, text FROM documents WHERE is_ai=1"
        ).fetchall()
        con.execute("DELETE FROM relations")
        con.execute("DELETE FROM document_entities")
        con.execute("DELETE FROM entities")
        con.execute(
            "DELETE FROM name_verifications WHERE evidence_type='source_title'"
        )
        con.execute(
            """
            DELETE FROM name_verifications
            WHERE evidence_type='transcript' AND status='pending'
            """
        )

        for doc in docs:
            title_repos = _repository_candidates(doc["title"])
            transcript_text = doc["text"].split("\n\n[视频画面 OCR]\n", 1)[0]
            transcript_repos = _repository_candidates(transcript_text)
            entities: dict[tuple[str, str], tuple[str, float]] = {}
            for name in title_repos:
                now = datetime.now(timezone.utc).isoformat()
                con.execute(
                    """
                    INSERT INTO name_verifications(
                      document_id, surface_form, canonical_name, kind,
                      evidence_type, evidence_ref, status, created_at, updated_at
                    ) VALUES (?, ?, ?, 'repository', 'source_title', ?, 'verified', ?, ?)
                    ON CONFLICT(document_id, surface_form, kind) DO UPDATE SET
                      canonical_name=excluded.canonical_name,
                      evidence_type='source_title',
                      evidence_ref=excluded.evidence_ref,
                      status='verified',
                      updated_at=excluded.updated_at
                    """,
                    (doc["id"], name, name, doc["url"], now, now),
                )
                entities[(name.casefold(), "repository")] = (name, 2.0)
            for name in transcript_repos - title_repos:
                now = datetime.now(timezone.utc).isoformat()
                con.execute(
                    """
                    INSERT INTO name_verifications(
                      document_id, surface_form, kind, evidence_type,
                      status, created_at, updated_at
                    ) VALUES (?, ?, 'repository', 'transcript', 'pending', ?, ?)
                    ON CONFLICT(document_id, surface_form, kind) DO NOTHING
                    """,
                    (doc["id"], name, now, now),
                )
                verified = con.execute(
                    """
                    SELECT canonical_name
                    FROM name_verifications
                    WHERE document_id=? AND surface_form=? AND kind='repository'
                      AND status='verified'
                    """,
                    (doc["id"], name),
                ).fetchone()
                if verified:
                    canonical = verified["canonical_name"] or name
                    entities[(canonical.casefold(), "repository")] = (canonical, 2.0)

            verified_names = con.execute(
                """
                SELECT canonical_name, surface_form, kind
                FROM name_verifications
                WHERE document_id=? AND status='verified'
                """,
                (doc["id"],),
            ).fetchall()
            for item in verified_names:
                name = item["canonical_name"] or item["surface_form"]
                entities[(name.casefold(), item["kind"])] = (name, 2.0)

            corpus = f"{doc['title']}\n{doc['text']}"
            for name in HASHTAG.findall(corpus):
                if not any(char.isalpha() for char in name):
                    continue
                entities[(name.casefold(), "tag")] = (name, 1.0)
            for name in TERMS.findall(corpus):
                entities[(name.casefold(), "concept")] = (name, 1.5)

            entity_ids: list[int] = []
            doc_node = f"doc:{doc['source_id']}"
            graph.add_node(
                doc_node, kind="document", label=doc["title"], url=doc["url"] or ""
            )
            for (_, kind), (name, weight) in entities.items():
                con.execute(
                    "INSERT OR IGNORE INTO entities(name, kind) VALUES (?, ?)", (name, kind)
                )
                entity_id = int(
                    con.execute(
                        "SELECT id FROM entities WHERE name=? AND kind=?", (name, kind)
                    ).fetchone()["id"]
                )
                entity_ids.append(entity_id)
                con.execute(
                    "INSERT INTO document_entities(document_id, entity_id, weight) VALUES (?, ?, ?)",
                    (doc["id"], entity_id, weight),
                )
                entity_node = f"{kind}:{name}"
                graph.add_node(entity_node, kind=kind, label=name)
                graph.add_edge(doc_node, entity_node, relation="mentions", weight=weight)

            for left, right in combinations(sorted(set(entity_ids)), 2):
                con.execute(
                    """
                    INSERT INTO relations(
                      source_entity_id, target_entity_id, relation, document_id, weight
                    ) VALUES (?, ?, 'co-occurs', ?, 1)
                    """,
                    (left, right, doc["id"]),
                )

    payload = nx.node_link_data(graph, edges="edges")
    json_path = SETTINGS.graph / "knowledge_graph.json"
    graphml_path = SETTINGS.graph / "knowledge_graph.graphml"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    nx.write_graphml(graph, graphml_path)
    return {
        "documents": len(docs),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "json": str(json_path),
        "graphml": str(graphml_path),
    }


def related(name: str, limit: int = 10) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT e2.name, e2.kind, SUM(r.weight) AS weight
            FROM entities e1
            JOIN relations r
              ON r.source_entity_id=e1.id OR r.target_entity_id=e1.id
            JOIN entities e2
              ON e2.id=CASE
                WHEN r.source_entity_id=e1.id THEN r.target_entity_id
                ELSE r.source_entity_id
              END
            WHERE e1.name LIKE ?
            GROUP BY e2.id
            ORDER BY
              weight DESC,
              CASE e2.kind
                WHEN 'repository' THEN 0
                WHEN 'project' THEN 1
                WHEN 'concept' THEN 2
                ELSE 3
              END,
              e2.name
            LIMIT ?
            """,
            (name, limit),
        ).fetchall()
    return [dict(row) for row in rows]
