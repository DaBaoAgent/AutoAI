"""AutoAI 每日 AI 看板采集器 — 用 Agent-Reach 全平台拉取当天最新的
博主推荐开源项目 / 优质技能 / AI圈新闻，整理入库并生成 HTML 看板简报。

渠道（Agent-Reach 后端，全部无登录态）：
  - GitHub API       → 当天新建 AI 开源项目
  - Exa (mcporter)   → AI 新闻 / 博主推荐 / 技能教程
  - B站 (bili-cli)   → 博主推荐视频（AI 开源 / AI 工具）
  - V2EX API         → AI 圈热门讨论
  - Hacker News API  → AI 相关热帖

输出：
  - 入库：source_type="agent-reach", source_id="agent-reach-daily-YYYY-MM-DD"
  - HTML 看板：data/tmp/agent_reach_daily_YYYY-MM-DD.html
"""
import json
import os
import re
import subprocess
import sys
import html
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

WORKDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(WORKDIR, "src"))
from dabo_kb.db import upsert_document, replace_chunks  # noqa: E402

# ---------- Agent-Reach 后端定位 ----------
HOME = os.path.expanduser("~")
BILI = os.path.join(HOME, "AppData", "Local", "pipx", "pipx", "venvs", "bilibili-cli", "Scripts", "bili.exe")
MCPORTER = None
for p in [os.path.join(os.environ.get("APPDATA", ""), "npm", "mcporter"), "mcporter"]:
    if os.path.isfile(p) or os.path.isfile(p + ".cmd"):
        MCPORTER = p
        break

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# 本机系统代理（V2EX/Exa/B站等国内受限渠道需要），脚本内自足不依赖调用方
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:15715")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:15715")

AI_KEYWORDS = re.compile(
    r"(?i)(\bai\b|人工智能|大模型|llm|agent|智能体|skill|技能|codex|claude|chatgpt|openai|"
    r"gemini|github|开源|vibe.?coding|comfyui|模型|aigc|deepseek|kimi|gpt|"
    r"anthropic|机器人|神经网络|机器学习|训练|推理|token|mcp|rag|编程|代码|developer)"
)


def http_get(url, headers=None, timeout=15):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def is_ai(text: str) -> bool:
    return bool(AI_KEYWORDS.search(text or ""))


def run_cmd(cmd, timeout=90):
    """跑外部 CLI（bili/mcporter），走系统代理。"""
    env = dict(os.environ)
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:15715")
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:15715")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
        return r.stdout
    except Exception as e:
        print(f"    cmd 失败 {cmd[0]}: {e}")
        return ""


# ---------- 各渠道采集 ----------

def fetch_github(days=4):
    """高星 AI 开源项目（stars:>1000）：近期活跃（pushed 索引延迟~3天，窗口 4 天）
    + 3 天窗口新建（捕捉爆发的新项目）"""
    items = []
    since_new = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    since_hot = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    queries = [
        f"topic:ai pushed:>{since_hot} stars:>1000", "hot",
        f"topic:agent pushed:>{since_hot} stars:>1000", "hot",
        f"ai skill pushed:>{since_hot} stars:>1000", "hot",
        f"topic:ai created:>{since_new} stars:>1000", "new",
    ]
    for i in range(0, len(queries), 2):
        q, kind = queries[i], queries[i + 1]
        try:
            d = json.loads(http_get("https://api.github.com/search/repositories?q="
                                    + urllib.parse.quote(q) + "&sort=stars&order=desc&per_page=12",
                                    headers={"Accept": "application/vnd.github+json"}))
            for it in d.get("items", []):
                items.append({
                    "title": it["full_name"], "url": it["html_url"],
                    "desc": (it.get("description") or "")[:140],
                    "platform": "GitHub", "heat": f"⭐{it['stargazers_count']}",
                    "extra": f"{it.get('language') or '?'} · {'今日活跃' if kind == 'hot' else '新建'}",
                    "category": "skills" if re.search(r"(?i)skill", it.get("description") or "") else "open_source",
                })
        except Exception as e:
            print(f"    github err: {e}")
    return items


def fetch_exa():
    """Exa 搜索：AI 新闻 / 博主推荐 / 技能教程（mcporter 文本输出逐块解析）
    GitHub 仓库要求 stars>=1000，新闻/文章类保留"""
    items = []
    if not MCPORTER:
        print("    mcporter 不可用，跳过 Exa")
        return items
    queries = [
        ("AI news today", "news"),
        ("best open source AI projects recommended", "open_source"),
        ("AI 工具 推荐 教程", "skills"),
        ("AI agent skill tutorial", "skills"),
        ("AI 圈 今天 大新闻", "news"),
    ]
    for q, cat in queries:
        # npm 全局 bin 是 .cmd shim，需经 cmd.exe 执行
        out = run_cmd(["cmd", "/c", MCPORTER, "call", "exa.web_search_exa", f"query={q}", "numResults=5"])
        for blk in re.split(r"(?=^Title:)", out, flags=re.M):
            m = re.match(r"Title:\s*(.+?)\nURL:\s*(\S+)", blk)
            if not m:
                continue
            title, url = m.group(1).strip(), m.group(2).strip()
            if not is_ai(title):
                continue
            stars_m = re.search(r"Stars:\s*([\d,]+)", blk)
            stars = int(stars_m.group(1).replace(",", "")) if stars_m else 0
            if "github.com" in url and stars and stars < 1000:
                continue  # GitHub 项目高星过滤
            # desc：取 Highlights 中第一条 >20 字符的正文行
            desc = ""
            for ln in blk.splitlines():
                s = ln.strip()
                if (s and not s.startswith(("Title:", "URL:", "Published:", "Author:", "Highlights:"))
                        and not s.startswith(("-", "#", "*", ">")) and len(s) > 20):
                    desc = s[:140]
                    break
            items.append({
                "title": title[:120], "url": url.strip(),
                "desc": desc, "platform": "Exa",
                "heat": f"⭐{stars}" if stars else "",
                "extra": f"Exa·{q[:14]}", "category": cat,
            })
    return items


def fetch_bilibili():
    """B站博主推荐：搜索 AI 开源 / AI 工具 最新视频"""
    items = []
    if not os.path.isfile(BILI):
        print("    bili-cli 不可用，跳过 B站")
        return items
    for q in ["AI 开源", "AI 工具"]:
        out = run_cmd([BILI, "search", q, "--type", "video", "-n", "8"])
        try:
            import yaml
            d = yaml.safe_load(out) or {}
            for r in d.get("data") or []:
                title = r.get("title", "")
                if r.get("play", 0) < 2000:
                    continue  # 低播放搬运/噪音过滤
                if is_ai(title):
                    items.append({
                        "title": title.strip()[:120],
                        "url": f"https://www.bilibili.com/video/{r.get('bvid')}",
                        "desc": "",
                        "platform": "B站",
                        "heat": f"▶{r.get('play', 0)}",
                        "extra": f"UP:{r.get('author', '?')}",
                        "category": "skills",
                    })
        except Exception as e:
            print(f"    bili 解析失败: {e}")
    return items


def fetch_v2ex():
    """V2EX 热门主题（AI 相关）"""
    items = []
    try:
        d = json.loads(http_get("https://www.v2ex.com/api/topics/hot.json",
                                headers={"User-Agent": "agent-reach/1.0"}))
        for t in d[:30]:
            title = t.get("title", "")
            node = t.get("node") or {}
            if node.get("name") == "promotions" or node.get("title") == "推广":
                continue
            if is_ai(title):
                items.append({
                    "title": title[:120], "url": f"https://www.v2ex.com/t/{t.get('id')}",
                    "desc": "", "platform": "V2EX", "heat": f"💬{t.get('replies', 0)}",
                    "extra": f"节点:{t.get('node', {}).get('title', '?')}", "category": "news",
                })
    except Exception as e:
        print(f"    v2ex err: {e}")
    return items


def fetch_hn():
    """Hacker News 热帖（AI 相关）"""
    items = []
    try:
        ids = json.loads(http_get("https://hacker-news.firebaseio.com/v0/topstories.json"))
        for i in ids[:40]:
            try:
                it = json.loads(http_get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json"))
                title = it.get("title", "")
                if is_ai(title):
                    items.append({
                        "title": title[:120],
                        "url": it.get("url") or f"https://news.ycombinator.com/item?id={i}",
                        "desc": (it.get("text") or "")[:140],
                        "platform": "HN", "heat": f"▲{it.get('score', 0)}",
                        "extra": "Hacker News", "category": "news",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"    hn err: {e}")
    return items


# ---------- 中文简介 + 收录理由（DashScope qwen） ----------

def read_dashscope_key():
    """从 HERMES_HOME/.env（或 ~/.hermes/.env）读 DASHSCOPE_API_KEY"""
    candidates = [
        os.path.join(os.environ.get("HERMES_HOME", r"C:\Users\xxx13\AppData\Local\hermes"), ".env"),
        os.path.expanduser("~/.hermes/.env"),
    ]
    for p in candidates:
        try:
            if os.path.exists(p):
                for line in open(p, encoding="utf-8", errors="replace"):
                    if line.startswith("DASHSCOPE_API_KEY="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return None


def enrich_zh(items, batch_size=20):
    """批量生成中文简介 zh_desc + 收录理由 reason（DashScope qwen-plus）。
    小批量+重试；某批失败则该批降级用原文，不影响其他批。"""
    if not items:
        return items
    key = read_dashscope_key()
    if not key:
        print("  [警告] 无 DASHSCOPE_API_KEY，跳过中文简介（降级用原文）")
        for it in items:
            it["zh_desc"] = (it.get("desc") or "")[:200]
            it["reason"] = "高热度/高价值内容"
        return items

    def _one_batch(batch):
        payload = [{"title": it["title"], "desc": it.get("desc", "")[:140],
                    "platform": it["platform"], "extra": it.get("extra", "")} for it in batch]
        prompt = (
            "你是AI资讯整理助手。输入JSON数组，每项含 title/desc/platform/extra（desc可能为空，可能为英文）。"
            "为每项输出：zh_desc=中文简介，**必须写满80个汉字以上（150字左右最佳）**——把desc翻译扩展成通顺的中文介绍，"
            "desc为空则根据title/extra扩写，说清楚这个项目/内容是什么、核心功能、解决什么问题；"
            "reason=收录理由，**必须写满30个汉字以上（50字左右最佳）**——结合热度/新颖性/实用性/技术价值说明为什么值得关注，口语化中文，不要说空话。"
            "字数不达标视为不合格，宁长勿短。"
            "严格输出JSON对象{\"items\":[{\"zh_desc\":\"...\",\"reason\":\"...\"}]}，与输入一一对应，不要任何多余文字。"
        )
        body = {"model": "qwen-plus", "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ], "response_format": {"type": "json_object"}}
        req = urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        # 本机直连 dashscope 偶发 SSL 握手超时，走系统代理最稳
        proxy = urllib.request.ProxyHandler(
            {"http": "http://127.0.0.1:15715", "https": "http://127.0.0.1:15715"})
        opener = urllib.request.build_opener(proxy)
        resp = opener.open(req, timeout=180)
        d = json.loads(resp.read().decode("utf-8"))
        content = d["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed = parsed.get("items") or parsed.get("data") or []
        for it, en in zip(batch, parsed):
            if isinstance(en, dict):
                it["zh_desc"] = str(en.get("zh_desc") or it.get("desc") or "")[:200]
                it["reason"] = str(en.get("reason") or "高热度/高价值内容")[:80]

    ok_total = 0
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        done = False
        for attempt in range(3):
            try:
                _one_batch(batch)
                done = True
                break
            except Exception as e:
                print(f"  [中文简介] 批次{start//batch_size+1} 第{attempt+1}次失败: {e}")
                import time
                time.sleep(3 * (attempt + 1))
        if not done:
            print(f"  [中文简介] 批次{start//batch_size+1} 重试3次仍失败，该批降级用原文")
            for it in batch:
                it["zh_desc"] = (it.get("desc") or "")[:200]
                it["reason"] = "高热度/高价值内容"
        else:
            ok_total += len(batch)
    print(f"  [中文简介] 成功 {ok_total}/{len(items)} 条")
    return items


# ---------- 看板 HTML ----------

HTML_TMPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@TITLE@</title>
<script>
function copyRepo(btn, name){
  var done = function(){ btn.textContent = '✓ 已复制 ' + name; setTimeout(function(){ btn.textContent = '📋 复制仓库名'; }, 1600); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(name).then(done, function(){ fallbackCopy(btn, name, done); });
  } else { fallbackCopy(btn, name, done); }
}
function fallbackCopy(btn, name, done){
  var ta = document.createElement('textarea');
  ta.value = name; document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch(e) { alert('复制失败，请手动复制：' + name); }
  document.body.removeChild(ta);
}
</script></head>
<body style="margin:0;padding:0;background:#0f1420;font-family:'Segoe UI',PingFang SC,Microsoft YaHei,sans-serif;color:#e6edf3">
<div style="max-width:760px;margin:0 auto;padding:24px 16px">
  <div style="background:linear-gradient(135deg,#1f6feb 0%,#8250df 100%);border-radius:16px;padding:22px 26px;margin-bottom:22px">
    <div style="font-size:26px;font-weight:700;color:#fff">@TITLE@</div>
    <div style="color:rgba(255,255,255,.85);font-size:13px;margin-top:6px">@SUBTITLE@</div>
  </div>
@SECTIONS@
  <div style="text-align:center;color:#6e7681;font-size:12px;margin-top:26px;padding-bottom:10px">
    AutoAI 每日看板 · 数据源：Agent-Reach（GitHub/Exa/B站/V2EX/HN）· @DATE@
  </div>
</div></body></html>"""

SECTION_HEAD = """  <div style="background:#1c2333;border-radius:14px;padding:16px 20px;margin-bottom:18px">
    <div style="font-size:18px;font-weight:700;color:#58a6ff;margin-bottom:2px">{emoji} {name}
      <span style="font-size:12px;color:#8b949e;font-weight:400;margin-left:8px">{count} 条</span></div>
    <div style="font-size:12px;color:#6e7681;margin-bottom:12px">{desc}</div>
{cards}
  </div>"""

CARD = """    <div style="background:#232b3d;border-radius:10px;padding:12px 14px;margin-bottom:10px;border-left:3px solid {color}">
      <div style="font-size:14px;line-height:1.45">
        <a href="{url}" style="color:#e6edf3;text-decoration:none;font-weight:600">{title}</a>
        <span style="color:#8b949e;font-size:12px;margin-left:6px">{platform}</span></div>
      <div style="color:#8b949e;font-size:12px;margin-top:4px">{extra}{heat}</div>
      {desc_html}
      {reason_html}
      <div style="margin-top:7px;background:#0f1420;border:1px solid #30363d;border-radius:8px;padding:6px 10px;display:flex;align-items:center;gap:8px">
        <code onclick="this.select()" title="点击全选，Ctrl+C 复制"
          style="flex:1;color:#58a6ff;font-size:13px;font-family:Consolas,monospace;cursor:pointer;user-select:all;-webkit-user-select:all">{repo}</code>
        <button onclick="copyRepo(this, '{repo_esc}')"
          style="background:#1f6feb;color:#fff;border:none;border-radius:6px;padding:4px 12px;font-size:12px;cursor:pointer;font-family:inherit;flex-shrink:0">📋 复制</button>
      </div>
    </div>"""


def build_html(items, date):
    sections = [
        ("🔥", "开源项目", "博主/社区推荐的当天新建 AI 开源项目", "#3fb950", "open_source"),
        ("💡", "优质技能 / 工具", "AI 技能、工具与博主推荐教程", "#d29922", "skills"),
        ("📰", "AI 圈新闻", "AI 圈当天动态、热门讨论", "#f85149", "news"),
    ]
    section_html = []
    total = 0
    for emoji, name, desc, color, cat in sections:
        sel = [it for it in items if it["category"] == cat]
        total += len(sel)
        if not sel:
            sel = []
        cards = []
        for i, it in enumerate(sel[:25], 1):
            heat = f" · {it['heat']}" if it.get("heat") else ""
            desc_html = ""
            if it.get("zh_desc"):
                desc_html = f'<div style="color:#9da7b3;font-size:12px;margin-top:4px">{it["zh_desc"]}</div>'
            reason_html = ""
            if it.get("reason"):
                reason_html = f'<div style="color:#7ee787;font-size:12px;margin-top:3px">📌 收录理由：{it["reason"]}</div>'
            repo_esc = it["title"].replace("'", "&#39;")
            cards.append(CARD.format(url=it["url"], title=it["title"],
                                     platform=it["platform"], extra=it["extra"] + heat,
                                     heat="", desc_html=desc_html,
                                     reason_html=reason_html, repo=html.escape(it["title"]),
                                     repo_esc=repo_esc, color=color))
        section_html.append(SECTION_HEAD.format(emoji=emoji, name=name, desc=desc,
                                                count=len(sel), cards="\n".join(cards) if cards else
                                                "    <div style='color:#6e7681;font-size:13px'>今日暂无</div>"))
    title = f"🧠 AutoAI 每日 AI 看板 — {date}"
    subtitle = f"博主推荐开源项目 / 优质技能 / AI圈新闻 · 共 {total} 条"
    return (HTML_TMPL.replace("@TITLE@", title).replace("@SUBTITLE@", subtitle)
                    .replace("@DATE@", date).replace("@SECTIONS@", "\n".join(section_html)))


# ---------- 主流程 ----------

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== AutoAI 每日 AI 看板采集 {today} ===")

    all_items = []
    for name, fn in [("GitHub", fetch_github), ("Exa", fetch_exa),
                     ("B站", fetch_bilibili), ("V2EX", fetch_v2ex), ("HN", fetch_hn)]:
        try:
            got = fn()
            print(f"  [{name}] {len(got)} 条")
            all_items.extend(got)
        except Exception as e:
            print(f"  [{name}] FAILED: {e}")

    # 去重（按 title 前 60 字符）
    seen, uniq = set(), []
    for it in all_items:
        key = it["title"][:60]
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    print(f"  合计 {len(all_items)} → 去重后 {len(uniq)} 条")

    # 中文简介 + 收录理由
    enrich_zh(uniq)

    # 入库
    lines = [f"# AutoAI 每日 AI 看板 — {today}", ""]
    lines.append("> 数据源：Agent-Reach（GitHub / Exa / B站 / V2EX / Hacker News）当天最新，GitHub 项目要求 stars≥1000。")
    lines.append("")
    by_cat = {}
    for it in uniq:
        by_cat.setdefault(it["category"], []).append(it)
    detail = []
    for cat, items in by_cat.items():
        lines.append(f"## {cat}（{len(items)}）")
        lines.append("")
        for it in items[:20]:
            lines.append(f"- [{it['title']}]({it['url']}) — {it['platform']} {it['extra']}")
            detail.append(f"{cat} {it['title']}（{it['platform']} {it['extra']}）："
                          f"{it.get('zh_desc') or it.get('desc') or '无描述'}。"
                          f"收录理由：{it.get('reason', '')}，链接 {it['url']}")
        lines.append("")
    text = "\n".join(lines)
    source_id = f"agent-reach-daily-{today}"
    title = f"AutoAI 每日 AI 看板 {today}（Agent-Reach 全平台）"
    print("[入库] ...")
    doc_id = upsert_document(
        source_type="agent-reach", source_id=source_id, title=title,
        url="https://github.com/trending", author="agent-reach",
        is_ai=True, status="transcribed",
        metadata={"collected_at": datetime.now().isoformat(),
                  "channels": list({it["platform"] for it in uniq})},
    )
    chunks = [{"text": text, "start": None, "end": None}]
    for line in detail:
        chunks.append({"text": line, "start": None, "end": None})
    replace_chunks(doc_id, title, chunks)
    print(f"  doc_id={doc_id}, chunks={len(chunks)}")

    # 原始 JSON + HTML 看板
    raw = os.path.join(WORKDIR, "data", "tmp", f"agent_reach_raw_{today}.json")
    with open(raw, "w", encoding="utf-8") as f:
        json.dump({"title": title, "items": uniq}, f, ensure_ascii=False, indent=2)
    html_path = os.path.join(WORKDIR, "data", "tmp", f"agent_reach_daily_{today}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_html(uniq, today))
    print(f"  HTML 看板: {html_path}")

    # 预览
    print("\n[预览] 各分类 Top 5:")
    for it in uniq[:15]:
        print(f"  [{it['platform']}/{it['category']}] {it['title'][:55]} | {it['extra']}")

    if not uniq:
        print("\n⚠️ 今日 0 条（全部渠道失败？检查代理/网络后重试）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
