"""GitHub AI 热榜收录脚本：抓 API → 整理为文档 → 写入知识库 → 分块索引"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime

WORKDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WORKDIR, "src"))
from dabo_kb.db import upsert_document, replace_chunks, connect

def gh_api(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "dabo-kb"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())

def build_doc() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    # 1. 近7天新建项目（按 stars 排序）
    new_projects = gh_api(
        "https://api.github.com/search/repositories?q=created:%3E2026-07-26&sort=stars&order=desc&per_page=15"
    ).get("items", [])
    # 2. 本周活跃且高 star 的 AI 相关项目
    active = gh_api(
        "https://api.github.com/search/repositories?q=stars:%3E5000+pushed:%3E2026-07-26+ai+OR+agent+OR+llm&sort=stars&order=desc&per_page=15"
    ).get("items", [])

    lines = [f"# GitHub AI 热榜日报 — {today}（API自动收录）", ""]
    lines.append("> 来源：GitHub Search API，自动抓取整理。包含「近7天新建热门项目」与「本周活跃AI项目」两类。")
    lines.append("")

    lines.append("## 🔥 近7天新建热门项目 TOP15")
    lines.append("| # | 项目 | Stars | 语言 | 描述 |")
    lines.append("|---|------|-------|------|------|")
    for i, it in enumerate(new_projects[:15], 1):
        desc = (it.get("description") or "").strip().replace("|", "/")[:60]
        lang = it.get("language") or "?"
        lines.append(f"| {i} | [{it['full_name']}]({it['html_url']}) | ⭐{it['stargazers_count']:,} | {lang} | {desc} |")
    lines.append("")

    lines.append("## 🚀 本周活跃 AI 项目 TOP15（stars>5000）")
    lines.append("| # | 项目 | Stars | 语言 | 描述 |")
    lines.append("|---|------|-------|------|------|")
    for i, it in enumerate(active[:15], 1):
        desc = (it.get("description") or "").strip().replace("|", "/")[:60]
        lang = it.get("language") or "?"
        lines.append(f"| {i} | [{it['full_name']}]({it['html_url']}) | ⭐{it['stargazers_count']:,} | {lang} | {desc} |")
    lines.append("")

    # 合并明细文本用于分块检索
    detail = []
    for it in new_projects[:15]:
        detail.append(f"项目 {it['full_name']}（近7天新建，{it['stargazers_count']} 星，{it.get('language') or '未知语言'}）：{(it.get('description') or '无描述')}")
    for it in active[:15]:
        detail.append(f"项目 {it['full_name']}（本周活跃，{it['stargazers_count']} 星，{it.get('language') or '未知语言'}）：{(it.get('description') or '无描述')}")

    text = "\n".join(lines)
    return {
        "title": f"GitHub AI 热榜日报 {today}（API收录）",
        "source_id": f"github-trending-{today}",
        "text": text,
        "detail": detail,
        "url": "https://github.com/trending",
        "date": today,
    }

def chunk_text(doc: dict, chunk_chars: int = 300) -> list[dict]:
    """把文档切成检索块：标题行 + 明细行，按行聚合"""
    chunks = []
    # 表头部分作为整块
    chunks.append({"text": doc["text"], "start": None, "end": None})
    # 明细逐条分块（每条一个 chunk，便于精准检索）
    for line in doc["detail"]:
        chunks.append({"text": line, "start": None, "end": None})
    return chunks

def main():
    print("[1] 抓取 GitHub API...")
    doc = build_doc()
    print(f"  title: {doc['title']}")
    print(f"  明细条数: {len(doc['detail'])}")

    print("[2] 写入知识库...")
    doc_id = upsert_document(
        source_type="github",
        source_id=doc["source_id"],
        title=doc["title"],
        url=doc["url"],
        author="GitHub API",
        is_ai=True,
        status="transcribed",
        metadata={"collected_at": datetime.now().isoformat(), "channel": "auto-trending"},
    )
    chunks = chunk_text(doc)
    replace_chunks(doc_id, doc["title"], chunks)
    print(f"  doc_id={doc_id}, chunks={len(chunks)}")

    print("[3] 验证...")
    with connect() as con:
        row = con.execute("SELECT id, title, status FROM documents WHERE id=?", (doc_id,)).fetchone()
        cnt = con.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (doc_id,)).fetchone()[0]
        print(f"  DB: {dict(row)}, chunks={cnt}")

if __name__ == "__main__":
    main()
