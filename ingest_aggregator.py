"""全平台 AI 热点聚合入库：GitHub API + HN + B站 + 微博 + 抖音 → 过滤AI相关 → 写入知识库"""
import json, os, sys, re, time, urllib.request, urllib.parse
from datetime import datetime

WORKDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WORKDIR, "src"))
from dabo_kb.db import upsert_document, replace_chunks, connect

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

AI_KEYWORDS = re.compile(
    r"(?i)(\bai\b|人工智能|大模型|llm|agent|智能体|skill|codex|claude|chatgpt|openai|"
    r"gemini|github|开源|vibe.?coding|comfyui|模型|aigc|deepseek|kimi|fable|gpt|"
    r"anthropic|机器人|量化|神经网络|机器学习|训练|推理|token|mcp|rag)"
)

def http_get(url, headers=None, timeout=15):
    h = {"User-Agent": UA}
    if headers: h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")

def is_ai(text: str) -> bool:
    return bool(AI_KEYWORDS.search(text))

# ---------- 各平台抓取 ----------

def fetch_github():
    items = []
    for q in [
        "created:%3E2026-07-26&sort=stars&order=desc&per_page=15",
        "topic:ai+created:%3E2026-07-26&sort=stars&order=desc&per_page=10",
    ]:
        try:
            d = json.loads(http_get(f"https://api.github.com/search/repositories?q={q}",
                                    headers={"Accept": "application/vnd.github+json"}))
            for it in d.get("items", []):
                items.append({
                    "platform": "GitHub", "title": it["full_name"],
                    "desc": (it.get("description") or "")[:120],
                    "url": it["html_url"], "stars": it["stargazers_count"],
                    "lang": it.get("language") or "?", "extra": f"新建项目 {it['stargazers_count']}星",
                })
        except Exception as e:
            print(f"  github err: {e}")
    return items

def fetch_hn():
    items = []
    try:
        ids = json.loads(http_get("https://hacker-news.firebaseio.com/v0/topstories.json"))
        for i in ids[:40]:
            try:
                it = json.loads(http_get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json"))
                title = it.get("title", "")
                if is_ai(title):
                    items.append({
                        "platform": "HackerNews", "title": title,
                        "desc": (it.get("text") or "")[:120],
                        "url": it.get("url") or f"https://news.ycombinator.com/item?id={i}",
                        "stars": it.get("score", 0), "lang": "?", "extra": f"HN {it.get('score',0)}分",
                    })
            except Exception: continue
    except Exception as e:
        print(f"  hn err: {e}")
    return items

def fetch_bilibili():
    items = []
    try:
        d = json.loads(http_get("https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1",
                                headers={"Referer": "https://www.bilibili.com/"}))
        for r in (d.get("data") or {}).get("list") or []:
            title = r.get("title", "")
            if is_ai(title):
                items.append({
                    "platform": "B站", "title": title,
                    "desc": (r.get("desc") or "")[:120],
                    "url": f"https://www.bilibili.com/video/{r.get('bvid')}",
                    "stars": (r.get("stat") or {}).get("view", 0),
                    "lang": r.get("owner", {}).get("name", "?"),
                    "extra": f"B站 {(r.get('stat') or {}).get('view',0)}播放",
                })
    except Exception as e:
        print(f"  bilibili err: {e}")
    return items

def fetch_weibo():
    items = []
    try:
        d = json.loads(http_get("https://weibo.com/ajax/side/hotSearch",
                                headers={"Referer": "https://weibo.com/"}))
        for r in (d.get("data") or {}).get("realtime") or []:
            word = r.get("word", "")
            if is_ai(word):
                items.append({
                    "platform": "微博", "title": word,
                    "desc": "", "url": f"https://s.weibo.com/weibo?q={urllib.parse.quote(word)}",
                    "stars": r.get("raw_hot") or r.get("num") or 0,
                    "lang": "?", "extra": f"微博热 {r.get('num')}",
                })
    except Exception as e:
        print(f"  weibo err: {e}")
    return items

def fetch_douyin_hot():
    """从已保存的抖音热榜 JSON（由 playwright 抓取）读取"""
    items = []
    p = os.path.join(WORKDIR, "data", "tmp", "douyin_hot.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            for r in d.get("items", []):
                word = r.get("word", "")
                if is_ai(word):
                    items.append({
                        "platform": "抖音", "title": word,
                        "desc": "", "url": "https://www.douyin.com/hot",
                        "stars": r.get("hot_value", 0), "lang": "?",
                        "extra": f"抖音热 {r.get('hot_value',0)}",
                    })
        except Exception as e:
            print(f"  douyin hot err: {e}")
    return items

# ---------- 入库 ----------

def build_and_store():
    today = datetime.now().strftime("%Y-%m-%d")
    print("[1] 抓取各平台...")
    all_items = []
    for name, fn in [("GitHub", fetch_github), ("HackerNews", fetch_hn),
                     ("B站", fetch_bilibili), ("微博", fetch_weibo)]:
        try:
            got = fn()
            print(f"  {name}: {len(got)} 条AI相关")
            all_items.extend(got)
        except Exception as e:
            print(f"  {name} FAILED: {e}")

    # 去重（按 title）
    seen = set(); uniq = []
    for it in all_items:
        key = it["title"][:50]
        if key not in seen:
            seen.add(key); uniq.append(it)
    print(f"  合计 {len(uniq)} 条去重后")

    # 生成文档文本
    lines = [f"# 全平台 AI 热点聚合 — {today}", ""]
    lines.append("> 自动收录：GitHub / Hacker News / B站 / 微博 实时抓取，AI相关过滤。")
    lines.append("")
    by_platform = {}
    for it in uniq:
        by_platform.setdefault(it["platform"], []).append(it)
    detail = []
    for platform, items in by_platform.items():
        lines.append(f"## {platform}（{len(items)}）")
        lines.append("")
        for it in items[:20]:
            lines.append(f"- [{it['title']}]({it['url']}) — {it['extra']}")
            detail.append(f"{platform}项目 {it['title']}（{it['extra']}）：{it['desc'] or '无描述'}，链接 {it['url']}")
        lines.append("")

    text = "\n".join(lines)
    source_id = f"ai-hotspot-{today}"
    title = f"全平台 AI 热点聚合 {today}（API收录）"
    print("[2] 写入知识库...")
    doc_id = upsert_document(
        source_type="aggregator",
        source_id=source_id,
        title=title,
        url="https://github.com/trending",
        author="multi-platform",
        is_ai=True,
        status="transcribed",
        metadata={"collected_at": datetime.now().isoformat(), "platforms": list(by_platform.keys())},
    )
    chunks = [{"text": text, "start": None, "end": None}]
    for line in detail:
        chunks.append({"text": line, "start": None, "end": None})
    replace_chunks(doc_id, title, chunks)
    print(f"  doc_id={doc_id}, chunks={len(chunks)}")

    # 保存原始 JSON 供复核
    out = os.path.join(WORKDIR, "data", "tmp", f"aggregated_{today}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"title": title, "items": uniq}, f, ensure_ascii=False, indent=2)
    print(f"  原始数据: {out}")

    # 预览
    print("\n[3] 预览前20条:")
    for it in uniq[:20]:
        print(f"  [{it['platform']}] {it['title'][:50]} | {it['extra']}")

if __name__ == "__main__":
    build_and_store()
