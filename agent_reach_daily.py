#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AutoAI 每日 AI 看板 — v4.0（2026-08-24 重构）。

v4 相比 v3 的改动（对照 RSSHub/Huginn/changedetection.io/n8n 等优质项目）：
  1. SQLite 持久化（data/items.db）：历史可回查/统计；去重键 = URL 规范化 + 标题归一化，
     不再全量扫历史 JSON。
  2. 幂等重跑：当日 raw JSON 已存在且未 --force 时直接复用，不重采、不烧 token。
  3. 每源统计写 data/tmp/stats_YYYY-MM-DD.json（条数/错误/耗时/DeepSeek tokens/成本估算）。
  4. CLI 路径显式化：bili/mcporter 支持全路径或 PATH 探测，缺失时打 WARN 而不是静默空源。
  5. HN item 抓取改并发（8 workers，原串行 80 请求）；HN/V2EX 排序用时间衰减（HN 官方算法）。
  6. GitHub 默认走 Search API（原 trending 正则降为可选 --use-trending）。
  7. 阈值/模型/并发/关键词全部抽到 config.yaml。
  8. B站纪录片区关键词分级（strict 白名单 + soft 宽泛词需更高播放量，防跨题材混入）。
  9. RSS 2.0 输出（data/tmp/agent_reach_daily_YYYY-MM-DD.xml），看板可订阅。
 10. Provider 注册表：新增源 = 加一个 fetch_* 函数 + 注册一行。
 11. fetch() 响应大小上限（5MB）防御。

三大分区：
  1. 🔥 GitHub 精选：周涨幅候选池 → 排除已推荐 → DeepSeek 策展 2×10（novel + work）
  2. 🧠 AI 前沿动态：Hacker News + V2EX + Exa + B站 AI 搜索
  3. 🎬 纪录片 & 解说素材：B站热门/搜索 + Exa

中文简介（zh_desc ≥80字）+ 收录理由（reason ≥30字）：DeepSeek deepseek-chat，
key 读 HERMES_HOME/.env 的 DEEPSEEK_API_KEY，直连 api.deepseek.com，失败降级用原文。

产出：
  data/tmp/agent_reach_raw_YYYY-MM-DD.json   原始条目（唯一事实来源）
  data/tmp/agent_reach_daily_YYYY-MM-DD.html HTML 看板（用于邮件）
  data/tmp/agent_reach_daily_YYYY-MM-DD.xml  RSS 2.0 订阅源
  data/tmp/stats_YYYY-MM-DD.json             运行统计
  data/items.db                              SQLite 历史库

用法：
  python agent_reach_daily.py               # 采集 + 中文简介 + HTML + RSS（当日已有数据则幂等复用）
  python agent_reach_daily.py --force       # 强制重采当日
  python agent_reach_daily.py --skip-enrich # 只采集（不调 LLM）
  python agent_reach_daily.py --use-trending # GitHub 用 trending 正则通道（默认 Search API）
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta, datetime

import yaml

TODAY = date.today().isoformat()
BASE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(BASE, "data", "tmp")
DB_PATH = os.path.join(BASE, "data", "items.db")
os.makedirs(TMP, exist_ok=True)
RAW_PATH = os.path.join(TMP, f"agent_reach_raw_{TODAY}.json")
HTML_PATH = os.path.join(TMP, f"agent_reach_daily_{TODAY}.html")
RSS_PATH = os.path.join(TMP, f"agent_reach_daily_{TODAY}.xml")
STATS_PATH = os.path.join(TMP, f"stats_{TODAY}.json")

CONFIG_PATH = os.path.join(BASE, "config.yaml")


def load_config():
    """config.yaml + 环境变量覆盖；缺文件用内置默认。"""
    defaults = {
        "proxy": "http://127.0.0.1:15715",
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "fetch": {"timeout": 30, "max_bytes": 5242880},
        "deepseek": {"model": "deepseek-chat", "workers": 6, "retries": 3,
                     "max_tokens": 500, "temperature": 0.6,
                     "price_input_per_1m": 2.0, "price_output_per_1m": 8.0},  # 元/百万tokens（估算）
        "github": {"pool_size": 80, "curate_novel": 10, "curate_work": 10,
                   "use_trending_parse": False},
        "hn": {"top_n": 8, "min_score": 40, "scan_top": 80, "workers": 8},
        "v2ex": {"top_n": 8},
        "exa": {"num": 4, "timeout": 120,
                "ai_queries": ["new AI model release this week 2026",
                               "best new open source AI project"],
                "doc_queries": ["new documentary 2026 must watch",
                                "bilibili popular documentary 2026"]},
        "bili": {"cli": "bili", "hot_n": 40, "doc_hot_min_play": 20000,
                 "doc_soft_min_play": 50000, "ai_min_play": 2000,
                 "search_top_n": 5},
        "mcporter": {"cli": "mcporter"},
    }
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        for k, v in user_cfg.items():
            if isinstance(v, dict) and isinstance(defaults.get(k), dict):
                defaults[k].update(v)
            else:
                defaults[k] = v
    except FileNotFoundError:
        log(f"  ⚠️ 无 {CONFIG_PATH}，使用内置默认配置")
    except Exception as e:
        log(f"  ⚠️ config.yaml 读取失败({e})，使用内置默认配置")
    return defaults


CONFIG = load_config()
PROXY = CONFIG["proxy"]
UA = {"User-Agent": CONFIG["user_agent"]}
DS_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL = CONFIG["deepseek"]["model"]
RAW_ITEMS = []      # 全量条目（唯一事实来源）
STATS = {"date": TODAY, "started": datetime.now().isoformat(timespec="seconds"),
         "sources": {}, "deepseek": {"todo": 0, "ok": 0, "failed": 0,
                                     "prompt_tokens": 0, "completion_tokens": 0,
                                     "cost_est_rmb": 0.0}}


def log(msg):
    print(msg, flush=True)


def load_env_key(name):
    """从进程环境或 HERMES_HOME/.env 读 key。"""
    v = os.environ.get(name)
    if v:
        return v.strip()
    home = os.environ.get("HERMES_HOME") or r"C:\Users\xxx13\AppData\Local\hermes"
    env_path = os.path.join(home, ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
    except Exception as e:
        log(f"  ERR load_env({name}): {e}")
    return None


def fetch(url, timeout=None, max_bytes=None):
    """代理优先；代理不可达时直连回退；响应超 max_bytes 视为异常。"""
    timeout = timeout or CONFIG["fetch"]["timeout"]
    max_bytes = max_bytes or CONFIG["fetch"]["max_bytes"]
    last_err = None
    for proxy in (PROXY, None):
        try:
            handler = urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy} if proxy else {})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(url, headers=UA)
            data = opener.open(req, timeout=timeout).read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"响应超过 {max_bytes} 字节上限")
            return data.decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
    raise last_err


def run_cmd(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="ignore")


def resolve_cli(name, cfg_key, env_var=None):
    """显式解析外部 CLI 路径：环境变量 > config；是路径则校验存在，否则 which 探测。
    找不到时打 WARN（cron 环境下 PATH 可能缺），返回 None 由调用方处理空源。"""
    p = os.environ.get(env_var) if env_var else None
    if not p:
        p = CONFIG.get(cfg_key, {}).get("cli", "") if isinstance(
            CONFIG.get(cfg_key), dict) else CONFIG.get(cfg_key, "")
    if not p:
        log(f"  ⚠️ WARN: {name} CLI 未配置（config.{cfg_key}.cli）")
        return None
    if os.path.sep in p or "/" in p:
        if os.path.exists(p):
            return p
        log(f"  ⚠️ WARN: {name} CLI 路径不存在: {p}")
        return None
    found = shutil.which(p)
    if not found:
        log(f"  ⚠️ WARN: {name} CLI '{p}' 不在 PATH（cron 环境可能缺），该源将为空")
    return p


# ---------------------------------------------------------------- SQLite 持久化
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        platform TEXT, section TEXT, subgroup TEXT,
        title TEXT, url TEXT, desc TEXT, zh_desc TEXT, reason TEXT,
        score INTEGER DEFAULT 0, lang TEXT, week TEXT, stars TEXT,
        dedup_key TEXT NOT NULL UNIQUE,
        raw TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_date ON items(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_platform ON items(platform)")
    return conn


def url_norm(url):
    """URL 规范化去重键：统一大小写、去 query/fragment、平台内聚到 ID。"""
    u = (url or "").strip().lower()
    m = re.search(r'bilibili\.com/video/(bv\w+)', u)
    if m:
        return f"bili:{m.group(1)}"
    m = re.search(r'news\.ycombinator\.com/(?:item\?id=(\d+)|(\d+))', u)
    if m:
        return f"hn:{m.group(1) or m.group(2)}"
    m = re.search(r'github\.com/([^/\s]+/[^/\s]+)', u)
    if m:
        return f"github:{m.group(1)}"
    u = re.sub(r'[?#].*$', '', u).rstrip('/')
    return u


def title_norm(t):
    """标题归一化去重键：小写、去 emoji/符号、折叠空白。"""
    return re.sub(r'[^\w\u4e00-\u9fff]+', ' ', (t or "").lower()).strip()


def dedup_key_of(it):
    if it.get("url"):
        k = url_norm(it["url"])
        if k and not k.startswith(("bili:", "hn:", "github:")):
            return f"url:{k}"
        return k
    t = title_norm(it.get("title"))
    return f"title:{t[:60]}" if t else f"noop:{len(RAW_ITEMS)}"


def persist(conn, items, replace_day=False):
    """写入 SQLite：当日重跑时 UPDATE 补全 zh_desc/reason，其他日期 INSERT。"""
    n_new = n_upd = 0
    for it in items:
        key = dedup_key_of(it)
        row = (TODAY, it.get("platform", ""), it.get("section", ""),
               it.get("subgroup", ""), it.get("title", ""), it.get("url", ""),
               it.get("desc", ""), it.get("zh_desc", ""), it.get("reason", ""),
               int(it.get("score", 0) or 0), it.get("lang", ""),
               it.get("week", ""), it.get("stars", ""), key,
               json.dumps(it, ensure_ascii=False))
        cur = conn.execute("SELECT id, zh_desc, reason FROM items WHERE dedup_key=? AND date=?",
                           (key, TODAY))
        exist = cur.fetchone()
        if exist:
            # 已有条目：只补全缺失的中文简介/理由
            if (not exist[1] and it.get("zh_desc")) or (not exist[2] and it.get("reason")):
                conn.execute("""UPDATE items SET zh_desc=COALESCE(?,zh_desc),
                                reason=COALESCE(?,reason), raw=? WHERE id=?""",
                             (it.get("zh_desc") or None, it.get("reason") or None,
                              json.dumps(it, ensure_ascii=False), exist[0]))
                n_upd += 1
        else:
            conn.execute("""INSERT INTO items(date,platform,section,subgroup,title,url,
                            desc,zh_desc,reason,score,lang,week,stars,dedup_key,raw)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
            n_new += 1
    conn.commit()
    return n_new, n_upd


def load_today_from_db(conn):
    """幂等复用：当日 raw JSON 已存在则直接加载（不重采不烧 token）。"""
    if os.path.exists(RAW_PATH):
        try:
            with open(RAW_PATH, encoding="utf-8") as f:
                d = json.load(f)
            items = d.get("items", []) if isinstance(d, dict) else d
            if items:
                # 用 DB 里可能更全的 zh_desc 补全
                for it in items:
                    if it.get("zh_desc"):
                        continue
                    cur = conn.execute(
                        "SELECT zh_desc, reason FROM items WHERE dedup_key=? AND date=?",
                        (dedup_key_of(it), TODAY))
                    r = cur.fetchone()
                    if r and r[0]:
                        it["zh_desc"], it["reason"] = r[0], r[1]
                log(f"[幂等] 复用今日已有数据 {len(items)} 条（--force 强制重采）")
                return items
        except Exception as e:
            log(f"  ⚠️ 当日数据复用失败({e})，重新采集")
    return None


def load_exclude(conn):
    """已推荐排除：SQLite 历史去重键集合（不含今天）+ legacy。"""
    ex = set(LEGACY_EXCLUDE)
    for (key,) in conn.execute("SELECT dedup_key FROM items WHERE date < ?", (TODAY,)):
        ex.add(key)
    # 兜底：老格式 raw JSON（v3 时期）里 GitHub 标题
    for f in glob.glob(os.path.join(TMP, "agent_reach_raw_*.json")):
        if TODAY in f:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
            for it in (d.get("items", []) if isinstance(d, dict) else d):
                if isinstance(it, dict) and it.get("platform") == "GitHub":
                    ex.add(f"github:{str(it.get('title', '')).lower()}")
        except Exception:
            pass
    return ex


# ---------------------------------------------------------------- GitHub 精选
GH_LANGS = ["python", "typescript", "javascript", "go", "rust", "shell", "cpp", "java"]
LEGACY_EXCLUDE = {
    "xai-org/mimo-code", "omnigent", "vercel/eve", "qwenaudio/qwen-audio-agent",
    "makecindy/cindy", "synthetic-sciences/openscience",
}
GH_API = "https://api.github.com/search/repositories"


def parse_trending(page, top=15):
    out = []
    for r in re.findall(r'<article class="Box-row">(.*?)</article>', page, re.S)[:top]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"]+)"', r)
        if not m:
            continue
        m2 = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', r, re.S)
        m3 = re.search(r'itemprop="programmingLanguage">([^<]+)<', r)
        m4 = re.search(r'([\d,]+)\s*stars?\s*(?:today|this week)', r)
        m5 = re.search(r'<a href="/[^"]+/stargazers"[^>]*>.*?</svg>\s*([\d,]+)', r, re.S)
        out.append({
            "repo": m.group(1).strip(),
            "desc": re.sub(r'<[^>]+>', '', m2.group(1)).strip() if m2 else "",
            "lang": m3.group(1).strip() if m3 else "",
            "week": m4.group(1) if m4 else "0",
            "stars": m5.group(1) if m5 else "0",
        })
    return out


def fetch_github_pool_api():
    """主通道（默认）：Search API 直连（国内可达）。近似周涨幅 = 近7天活跃高星 + 近14天新建。"""
    since7 = (date.today() - timedelta(days=7)).isoformat()
    since14 = (date.today() - timedelta(days=14)).isoformat()
    queries = [f"pushed:>{since7} stars:>200", f"created:>{since14} stars:>20"]
    pool = {}
    for q in queries:
        try:
            url = f"{GH_API}?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page=50"
            data = json.loads(fetch(url, timeout=25))
            for r in data.get("items", []):
                name = r.get("full_name") or ""
                if not name:
                    continue
                pool[name.lower()] = {
                    "repo": name, "desc": r.get("description") or "",
                    "lang": r.get("language") or "",
                    "week": "", "stars": str(r.get("stargazers_count") or 0),
                    "board": "API",
                }
        except Exception as e:
            log(f"  ERR API[{q[:20]}]: {e}")
    items = sorted(pool.values(), key=lambda x: -(int(x["stars"].replace(",", "") or 0)))
    log(f"[GitHub] API 池直连去重后 {len(items)} 个候选")
    return items[:CONFIG["github"]["pool_size"]]


def fetch_github_pool_trending():
    """可选通道：github.com/trending 正则解析（HTML 改版即挂，默认关闭）。"""
    urls = [("https://github.com/trending?since=weekly", "全站")]
    urls += [(f"https://github.com/trending/{lg}?since=weekly", lg) for lg in GH_LANGS]
    pool = {}
    ok = 0
    for url, label in urls:
        try:
            for it in parse_trending(fetch(url)):
                it["board"] = label
                key = it["repo"].lower()
                cur = pool.get(key)
                if cur is None or (int(it["week"].replace(",", "") or 0)
                                   > int(cur["week"].replace(",", "") or 0)):
                    pool[key] = it
            ok += 1
            log(f"  OK {label} weekly")
        except Exception as e:
            log(f"  ERR {label}: {e}")
    items = sorted(pool.values(), key=lambda x: -(int(x["week"].replace(",", "") or 0)))
    log(f"[GitHub] 周涨幅池 {ok}/{len(urls)} 榜，去重后 {len(items)} 个候选")
    if ok == 0 and not items:
        log("[GitHub] ⚠️ trending 全挂 → 回退 Search API")
        return fetch_github_pool_api()
    return items[:CONFIG["github"]["pool_size"]]


def fetch_github_pool():
    if CONFIG["github"].get("use_trending_parse"):
        return fetch_github_pool_trending()
    return fetch_github_pool_api()


GH_CURATE_PROMPT = """你是GitHub开源项目策展人，用户是中文自媒体创作者（纪录片解说、口播文案、AI工具重度用户、本地知识库与视频制作工作流）。
候选列表 = 本周GitHub星标涨幅最快的项目（格式：repo | 本周★ | 一句话简介）。
从中各选 {novel_n} 个，共 {work_n} 个：
1. novel —— 最新奇/有趣的项目：优先个人/新项目、技能类、冷门实用工具；排除早已出名的老牌大项目（如 system-design-primer、ComfyUI、ollama、free-programming-books 等），即使它们涨幅最高。
2. work —— 与用户工作/兴趣最相关的：AI 工具/Agent、口播文案与自媒体创作工具、视频制作/字幕/配音/解说、纪录片素材、知识库/检索、效率工具、Windows 工具、中文内容相关。
只输出 JSON：{{"novel": [{{"repo": "owner/repo", "reason": "≤40字入选理由"}}], "work": [{{"repo": "owner/repo", "reason": "≤40字入选理由"}}]}}
候选列表：
{items}"""


def curate_github(items, exclude):
    """DeepSeek 策展：从候选池选 2×N。失败降级：按星标/周涨幅取前 2N。"""
    gcfg = CONFIG["github"]
    novel_n, work_n = gcfg["curate_novel"], gcfg["curate_work"]
    cands = [it for it in items if it["repo"].lower() not in exclude]
    if len(cands) < novel_n + work_n:
        cands = items
    lines = "\n".join(f"{it['repo']} | {it['week'] or it['stars']}★ | {it['desc'][:120]}"
                      for it in cands)
    key = load_env_key("DEEPSEEK_API_KEY")
    result = None
    if key:
        body = json.dumps({
            "model": DS_MODEL,
            "messages": [{"role": "user", "content": GH_CURATE_PROMPT
                          .replace("{novel_n}", str(novel_n))
                          .replace("{work_n}", str(work_n))
                          .replace("{items}", lines)}],
            "max_tokens": 1500, "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        try:
            req = urllib.request.Request(DS_URL, data=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = json.loads(data["choices"][0]["message"]["content"])
            if not isinstance(result, dict) or not result.get("novel") or not result.get("work"):
                result = None
        except Exception as e:
            log(f"  ERR 策展: {e}")
    if result is None:
        log("  ⚠️ 策展失败，降级：按热度取前 N")
        by_repo = {it["repo"].lower(): it for it in cands}
        result = {"novel": [{"repo": c["repo"], "reason": "本周星标涨幅榜前列"}
                            for c in cands[:novel_n]],
                  "work": [{"repo": c["repo"], "reason": "本周星标涨幅榜前列"}
                           for c in cands[novel_n:novel_n + work_n]]}
    return result, {it["repo"].lower(): it for it in items}


def build_github_section(curated, pool_map):
    """策展结果 → RAW_ITEMS（section=github）。返回条数。"""
    count = 0
    for group, sub in (("novel", "novel"), ("work", "work")):
        for sel in curated.get(group, []):
            it = pool_map.get(str(sel.get("repo", "")).lower())
            if not it:
                continue
            RAW_ITEMS.append({
                "platform": "GitHub", "section": "github", "subgroup": sub,
                "title": it["repo"], "url": f"https://github.com/{it['repo']}",
                "desc": it["desc"], "lang": it["lang"], "week": it["week"],
                "stars_total": it["stars"], "reason": str(sel.get("reason", ""))[:80],
            })
            count += 1
    log(f"[GitHub] 策展入选：新奇 {len(curated.get('novel', []))} + 工作 {len(curated.get('work', []))}")
    return count


# ---------------------------------------------------------------- Hacker News
HN_AI_RE = re.compile(r"\b(ai|llm|gpt|openai|anthropic|deepseek|machine learning|"
                      r"neural|model|agent|transformer|diffusion|genai|gemini|claude|"
                      r"ollama|llama|muse|robotics|computer vision|startup)\b", re.I)


def hn_hot(item):
    """Hacker News 官方 ranking 算法：热度 = (score-1) / (age_h + 2)^1.8"""
    age_h = (time.time() - item.get("time", time.time())) / 3600.0
    return (item.get("score", 0) - 1) / max((age_h + 2) ** 1.8, 1e-9)


def fetch_hn_ai(top_n=None):
    top_n = top_n or CONFIG["hn"]["top_n"]
    try:
        ids = json.loads(fetch("https://hacker-news.firebaseio.com/v0/topstories.json"))[
            :CONFIG["hn"]["scan_top"]]

        def get(i):
            try:
                return json.loads(fetch(f"https://hacker-news.firebaseio.com/v0/item/{i}.json"))
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=CONFIG["hn"]["workers"]) as ex:
            items = list(ex.map(get, ids))
        picked = []
        for it in items:
            if not it:
                continue
            title = it.get("title") or ""
            if HN_AI_RE.search(title) and it.get("score", 0) >= CONFIG["hn"]["min_score"]:
                picked.append({
                    "platform": "HN", "section": "ai", "title": title,
                    "url": it.get("url") or f"https://news.ycombinator.com/item?id={it.get('id')}",
                    "desc": f"score {it.get('score', 0)} · {it.get('descendants', 0)} 评论",
                    "score": it.get("score", 0), "hot": hn_hot(it),
                })
        picked.sort(key=lambda x: -x.get("hot", 0))
        out = picked[:top_n]
        RAW_ITEMS.extend(out)
        return len(out)
    except Exception as e:
        log(f"  ERR HN: {e}")
        return 0


# ---------------------------------------------------------------- V2EX
V2EX_AI_RE = re.compile(r"AI|GPT|LLM|大模型|机器学习|神经网络|OpenAI|Anthropic|DeepSeek|"
                        r"Agent|智能体|模型|Claude|Gemini|生成式", re.I)


def v2ex_hot(t):
    age_h = (time.time() - int(t.get("created", time.time()))) / 3600.0
    return t.get("replies", 0) / max((age_h + 2) ** 1.5, 1e-9)


def fetch_v2ex_ai(top_n=None):
    top_n = top_n or CONFIG["v2ex"]["top_n"]
    try:
        data = json.loads(fetch("https://www.v2ex.com/api/topics/hot.json"))
        picked = []
        for t in data:
            node = (t.get("node") or {}).get("name", "")
            if node == "promotions":
                continue
            title = t.get("title", "")
            if V2EX_AI_RE.search(title):
                picked.append({
                    "platform": "V2EX", "section": "ai", "title": title,
                    "url": t.get("url") or f"https://www.v2ex.com/t/{t.get('id')}",
                    "desc": f"回复 {t.get('replies', 0)} · 节点 {node}",
                    "score": t.get("replies", 0), "hot": v2ex_hot(t),
                })
        picked.sort(key=lambda x: -x.get("hot", 0))
        out = picked[:top_n]
        RAW_ITEMS.extend(out)
        return len(out)
    except Exception as e:
        log(f"  ERR V2EX: {e}")
        return 0


# ---------------------------------------------------------------- Exa (mcporter)
def exa_search(query, num=5):
    """返回 [{title,url,published,highlights}]；失败返回 []。"""
    cli = resolve_cli("mcporter", "mcporter")
    if not cli:
        return []
    # Windows 下 mcporter 是 .cmd shim，CreateProcess 无法直接执行 → 经 cmd /c 调起
    cmd = ["cmd", "/c", cli, "call", "--output", "json",
           f'exa.web_search_exa(query: "{query}", numResults: {num})']
    try:
        r = run_cmd(cmd, timeout=CONFIG["exa"]["timeout"])
        data = json.loads(r.stdout or "{}")
        text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text")
        out = []
        for b in re.split(r"\n---\s*\n", text):
            t = re.search(r"Title:\s*(.+)", b)
            u = re.search(r"URL:\s*(\S+)", b)
            p = re.search(r"Published:\s*([\d-]+)", b)
            hl = re.search(r"Highlights:\s*(.*)", b, re.S)
            if t and u:
                out.append({
                    "title": t.group(1).strip(),
                    "url": u.group(1).strip(),
                    "published": p.group(1) if p else "",
                    "highlights": re.sub(r"\s+", " ", hl.group(1)).strip()[:260] if hl else "",
                })
        return out
    except Exception as e:
        log(f"  ERR Exa[{query[:20]}...]: {e}")
        return []


def fetch_exa_ai():
    got = 0
    for q in CONFIG["exa"]["ai_queries"]:
        n = 0
        for it in exa_search(q, num=CONFIG["exa"]["num"]):
            if it["title"].lower() == it["url"].lower():
                continue
            RAW_ITEMS.append({"platform": "Exa", "section": "ai", "score": 0, **it})
            n += 1
        got += n
        log(f"  Exa[{q[:24]}...] +{n}")
    return got


def fetch_exa_doc():
    got = 0
    for q in CONFIG["exa"]["doc_queries"]:
        n = 0
        for it in exa_search(q, num=CONFIG["exa"]["num"]):
            if it["title"].lower() == it["url"].lower():
                continue
            RAW_ITEMS.append({"platform": "Exa", "section": "doc", "score": 0, **it})
            n += 1
        got += n
        log(f"  Exa[{q[:24]}...] +{n}")
    return got


# ---------------------------------------------------------------- B站 (bili-cli)
# strict：标题含明确纪录片信号；soft：宽泛题材词（命中需更高播放量，防跨题材高播放混入）
DOC_KW_STRICT = re.compile(r"纪录片|纪实|真实事件|真实记录|人文|考古|探秘|传奇|中国脊梁|"
                           r"制胜|真实生长|人间世|舌尖|航拍中国|国家地理|探索发现|走近科学|"
                           r"全纪实", re.I)
DOC_KW_SOFT = re.compile(r"历史|自然|动物|宇宙|地球|生命|战争|美食|地理|科学|人物|百态|"
                         r"动物世界|故宫|长城|黄河|长江", re.I)


def bili_hot_filtered(n=None, top_n=6):
    cli = resolve_cli("bili", "bili")
    if not cli:
        return []
    n = n or CONFIG["bili"]["hot_n"]
    try:
        r = run_cmd([cli, "hot", "-n", str(n)])
        data = yaml.safe_load(r.stdout) or {}
        items = (data.get("data") or {}).get("items") or []
        picked = []
        for it in items:
            title = it.get("title", "")
            view = (it.get("stats") or {}).get("view", 0)
            strict = DOC_KW_STRICT.search(title)
            soft = DOC_KW_SOFT.search(title)
            if strict and view >= CONFIG["bili"]["doc_hot_min_play"]:
                picked.append(it)
            elif soft and view >= CONFIG["bili"]["doc_soft_min_play"]:
                picked.append(it)
        picked.sort(key=lambda it: -((it.get("stats") or {}).get("view", 0)))
        out = []
        for it in picked[:top_n]:
            out.append({
                "platform": "B站", "section": "doc", "title": it.get("title", ""),
                "url": it.get("url") or f"https://www.bilibili.com/video/{it.get('bvid')}",
                "desc": f"播放 {(it.get('stats') or {}).get('view', 0)} · "
                        f"UP {((it.get('owner') or {}).get('name') or '')}",
                "score": (it.get("stats") or {}).get("view", 0),
            })
        return out
    except Exception as e:
        log(f"  ERR bili hot: {e}")
        return []


def bili_search(q, top_n=5, min_play=5000, section="doc"):
    cli = resolve_cli("bili", "bili")
    if not cli:
        return []
    try:
        r = run_cmd([cli, "search", q, "--type", "video", "-n", "20"])
        data = yaml.safe_load(r.stdout) or {}
        items = data.get("data") or []
        picked = []
        for it in items:
            title = it.get("title", "")
            play = it.get("play") or 0
            bvid = it.get("bvid") or it.get("id") or ""
            if isinstance(play, str):
                play = int(re.sub(r"\D", "", play) or 0)
            if play >= min_play and bvid:
                picked.append({
                    "platform": "B站", "section": section, "title": title,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "desc": f"播放 {play} · UP {it.get('author', '')}",
                    "score": play,
                })
        picked.sort(key=lambda x: -x.get("score", 0))
        return picked[:top_n]
    except Exception as e:
        log(f"  ERR bili search[{q}]: {e}")
        return []


def fetch_bilibili_doc():
    hot = bili_hot_filtered()
    s = bili_search("纪录片", top_n=CONFIG["bili"]["search_top_n"], min_play=5000)
    RAW_ITEMS.extend(hot + s)
    log(f"  bili 热门(纪录片向) {len(hot)} 条 + 搜索 {len(s)} 条")
    return len(hot) + len(s)


def fetch_bili_ai():
    out = bili_search("AI 工具", top_n=CONFIG["bili"]["search_top_n"],
                      min_play=CONFIG["bili"]["ai_min_play"], section="ai")
    RAW_ITEMS.extend(out)
    return len(out)


# ---------------------------------------------------------------- 精选有趣项目（固定注入，手写中文简介）
# 用户点名方向：像素办公室/语音陪伴/wifi雷达/世界模型/手机端侧LLM + 趣味项目。
# zh_desc 手写≥80字、reason≥30字 → enrich_zh 跳过（只处理缺 zh_desc 的），不烧 DeepSeek token。
CURATED_FUN = [
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "ringhyacinth/Star-Office-UI",
     "url": "https://github.com/ringhyacinth/Star-Office-UI", "desc": "像素风 AI 办公室看板",
     "lang": "", "week": "", "stars_total": "7443",
     "zh_desc": "一个像素风格的AI办公室看板，能把AI助手的工作状态实时可视化：谁在做什么、昨天做了什么、现在是否在线一目了然。支持多Agent协作、中英日三语界面、AI生图装修房间和桌面宠物模式，与OpenClaw深度集成时体验最佳，也可独立部署当状态看板使用，MIT协议开源。",
     "reason": "像素风治愈系看板，把枯燥的Agent工作状态变成可爱的小人办公室，可玩性极高，社区热度持续攀升。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "pixel-agents-hq/pixel-agents",
     "url": "https://github.com/pixel-agents-hq/pixel-agents", "desc": "终端AI编程Agent变成像素小人办公室",
     "lang": "", "week": "", "stars_total": "8999",
     "zh_desc": "把终端里正在运行的AI编程Agent变成像素小人，在迷你办公室里走来走去：写代码时坐桌前打字、搜索时看书、卡住等输入时会向你挥手示意。支持铺设地板墙壁、摆放家具、养像素宠物、64×64格大办公室，布局可导出导入JSON，让AI开发过程变得直观又有趣。",
     "reason": "程序员视角的趣味可视化，AI干活看得见摸得着，桌面宠物式交互极其解压，创意与实用兼备。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "pipecat-ai/pipecat",
     "url": "https://github.com/pipecat-ai/pipecat", "desc": "开源实时语音Agent框架",
     "lang": "", "week": "", "stars_total": "14536",
     "zh_desc": "由Daily公司维护的开源实时语音Agent框架，用于构建能听会说、支持随时打断和低延迟对话的语音AI应用，也可做多模态实时应用。提供开箱即用的语音流水线组件，支持WebRTC、流式STT/TTS与主流大模型接入，星标超1.4万，是语音陪伴、AI客服类产品的主流底座。",
     "reason": "语音Agent领域的事实标准框架之一，生态活跃，想做语音陪伴或客服产品可以直接站在巨人肩膀上。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "leon-ai/leon",
     "url": "https://github.com/leon-ai/leon", "desc": "开源个人AI语音助手",
     "lang": "", "week": "", "stars_total": "17455", "win": "需WSL",
     "zh_desc": "开源的个人AI语音助手Leon，支持语音交互、文本对话和自动化技能扩展，强调隐私与离线，不依赖商业云服务即可运行。内置Python/Node双语言技能开发接口，可自定义提醒、查询、自动化等技能，17k+星标，适合喜欢自己动手折腾的极客搭建专属私人管家。",
     "reason": "老牌开源语音助手，主打隐私与可扩展，给想要自托管陪伴式助手的用户一个可靠选择。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "ruvnet/ruview",
     "url": "https://github.com/ruvnet/ruview", "desc": "WiFi信号变雷达：穿墙感知存在/呼吸/姿态",
     "lang": "", "week": "", "stars_total": "91403",
     "zh_desc": "把普通WiFi信号变成雷达的开源平台：利用ESP32传感器采集信道状态信息(CSI)，就能穿墙感知人体存在、呼吸频率、动作姿态，甚至做生命体征监测，全程不拍一张照片、不装摄像头，天然规避隐私法规。2026年发布后迅速爆火，星标超9万，被视为WiFi感知领域的现象级项目。",
     "reason": "WiFi变雷达、穿墙看呼吸，硬核又科幻的玩法，隐私合规方向极具想象力，开源社区热议焦点。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "Saad-Imtiaz/WiFi-Sense",
     "url": "https://github.com/Saad-Imtiaz/WiFi-Sense", "desc": "ESP32-S3 WiFi被动雷达：人体存在检测",
     "lang": "", "week": "", "stars_total": "3",
     "zh_desc": "基于ESP32-S3的WiFi被动雷达小项目：只靠一个开发板和路由器，利用RSSI与信道状态信息(CSI)就能检测房间里有没有人、人在动还是静止、大概在什么位置，无需任何额外传感器。自带Web界面实时展示探测结果，Arduino工程开箱即用，是极客DIY智能家居感应器的绝佳入门。",
     "reason": "几十块钱的硬件就能做人体存在感知，DIY门槛极低，玩智能家居或安防监控非常实用。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "comfyanonymous/ComfyUI",
     "url": "https://github.com/comfyanonymous/ComfyUI", "desc": "节点式AI绘画工作流平台（Windows原生）",
     "lang": "", "week": "", "stars_total": "129378", "win": "可用",
     "zh_desc": "最流行的开源AI绘画工作流平台，用节点式拖拽界面把文生图、图生图、视频生成、模型微调等能力拼装成可视化流程，支持SD、Flux、Qwen-Image等几乎所有主流模型，Windows原生一键安装、CPU/GPU均可运行。12.9万星标，是AI创作者的必备工具，插件生态极其丰富。",
     "reason": "AI绘画的事实标准工作流工具，Windows原生支持极佳，插件生态庞大，创作者生产力天花板。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "ollama/ollama",
     "url": "https://github.com/ollama/ollama", "desc": "一行命令在本地跑大模型（Windows原生）",
     "lang": "", "week": "", "stars_total": "179281", "win": "可用",
     "zh_desc": "一行命令在本地跑大模型的最简工具：自动下载模型权重、启动推理服务，提供与OpenAI兼容的API，支持Qwen、GLM、Llama、DeepSeek等主流开源模型，Windows原生安装包即装即用，也可通过Docker部署。17.9万星标，把本地LLM的门槛降到极致，隐私数据不出本机。",
     "reason": "本地跑大模型最无脑的方案，Windows一键安装，配合Open WebUI还能拥有私人ChatGPT。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "mlc-ai/mlc-llm",
     "url": "https://github.com/mlc-ai/mlc-llm", "desc": "把LLM编译进手机/浏览器/嵌入式设备",
     "lang": "", "week": "", "stars_total": "23085",
     "zh_desc": "通用大模型部署引擎，核心思路是用机器学习编译技术把LLM编译成可在手机、平板、浏览器、嵌入式设备上高效运行的代码，支持iOS/Android/Web全平台，无需服务器即可在端侧流畅运行Qwen、Llama等主流模型。23k+星标，是把大模型塞进手机的事实标准方案之一。",
     "reason": "手机跑大模型的最成熟开源路线，隐私、离线、低延迟全都要，端侧AI时代的核心基建。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "ggml-org/llama.cpp",
     "url": "https://github.com/ggml-org/llama.cpp", "desc": "本地/手机跑LLM的事实标准推理引擎",
     "lang": "", "week": "", "stars_total": "125308",
     "zh_desc": "最流行的本地大模型推理引擎，用C/C++实现并高度优化，GGUF量化格式让几十亿参数模型也能跑在普通电脑甚至手机上，支持CPU/GPU/NPU各种硬件，生态覆盖桌面、Android、iOS、浏览器。12.5万星标，从边缘设备到数据中心的端侧推理事实标准。",
     "reason": "端侧AI绕不开的基石项目，想在本机或手机跑任何开源模型，第一个装的就是它。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "openclaw/openclaw",
     "url": "https://github.com/openclaw/openclaw", "desc": "2026现象级开源个人AI助手（养龙虾）",
     "lang": "", "week": "", "stars_total": "387268",
     "zh_desc": "2026年现象级开源个人AI助手，跨平台全终端运行，强调本地优先、能替你执行真实任务，像养一只龙虾一样陪着你干活。60天狂揽25万星、总星标超38万，创下GitHub历史增速纪录，把个人专属AI的概念推向大众，是今年开源圈绕不开的话题中心。",
     "reason": "GitHub历史上增长最快的开源项目，现象级热度，个人AI助手赛道的绝对王者。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "browser-use/browser-use",
     "url": "https://github.com/browser-use/browser-use", "desc": "让AI Agent像人一样操作浏览器（Windows可用）",
     "lang": "", "week": "", "stars_total": "110262", "win": "可用",
     "zh_desc": "让AI Agent像人一样操作浏览器的开源框架：给LLM一个任务，它就能自动打开网页、点击按钮、填写表单、抓取信息并完成复杂网页操作，支持主流大模型和浏览器，Windows上通过Python加pip即可安装使用。11万星标，是浏览器自动化Agent领域最火的项目。",
     "reason": "AI自动操作浏览器，做爬虫、比价、自动填表极其实用，Windows环境开箱即用。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "hacksider/Deep-Live-Cam",
     "url": "https://github.com/hacksider/Deep-Live-Cam", "desc": "一张照片实时换脸/Deepfake工具",
     "lang": "", "week": "", "stars_total": "96092",
     "zh_desc": "一键实时换脸工具：只需一张人脸照片就能在摄像头视频流中实时替换目标人脸，同时支持视频文件换脸和Deepfake生成，全程本地处理、无需高端显卡。9.6万星标，因其极低的门槛和惊艳效果风靡全网，也持续引发关于深度伪造技术的伦理讨论。",
     "reason": "实时换脸门槛降到一张照片级别，娱乐性拉满，是生成式AI最出圈的消费级应用之一。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "rasbt/LLMs-from-scratch",
     "url": "https://github.com/rasbt/LLMs-from-scratch", "desc": "从零手搓一个ChatGPT级LLM",
     "lang": "", "week": "", "stars_total": "103548",
     "zh_desc": "《Build a Large Language Model from Scratch》一书的官方代码库，手把手教你从零实现一个类ChatGPT的大语言模型：数据准备、注意力机制、预训练、微调、指令对齐全流程都有配套PyTorch代码和Jupyter教程，10万+星标，被誉为LLM入门第一课。",
     "reason": "从零到一吃透Transformer和大模型训练管线，理论实践结合的标杆教程，学习价值极高。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "TauricResearch/TradingAgents",
     "url": "https://github.com/TauricResearch/TradingAgents", "desc": "多Agent模拟华尔街投行团队炒股",
     "lang": "", "week": "", "stars_total": "99490",
     "zh_desc": "多智能体LLM金融交易框架，模拟华尔街投行团队分工：基本面分析师、情绪分析师、新闻分析师、基金经理、风控官等角色各司其职，在财报发布周期内协同研究、辩论并给出交易决策，99k+星标，是AI Agent加金融方向最火的实践项目。",
     "reason": "把多Agent协作玩出花，AI投资委员会概念新颖有趣，量化爱好者必看的明星项目。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "mindverse/Second-Me",
     "url": "https://github.com/mindverse/Second-Me", "desc": "训练一个自己的AI数字分身",
     "lang": "", "week": "", "stars_total": "15669",
     "zh_desc": "训练你自己的AI分身：通过分层记忆模型(HMM)和Me-Alignment算法，让AI学习你的思维方式和偏好，成为能代表你长期记忆、帮你做决策的数字自我。完全本地部署、数据不出设备，Apache-2.0开源，15k+星标，是数字永生概念的最佳实践之一。",
     "reason": "训练一个自己的AI的梦想成真，本地部署隐私可控，数字分身赛道的标杆项目。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "letta-ai/letta",
     "url": "https://github.com/letta-ai/letta", "desc": "有长期记忆的有状态Agent平台（原MemGPT）",
     "lang": "", "week": "", "stars_total": "24374",
     "zh_desc": "由MemGPT进化而来的有状态Agent平台，核心是给AI装上长期记忆：用Agent File(.af)格式保存状态、跨平台迁移、可版本控制，还提供可视化开发环境实时查看Agent的内存与工具调用，原生支持MCP，直击AI对话转头就忘的痛点。",
     "reason": "长期记忆是Agent落地的关键短板，Letta的.af格式与可视化调试独树一帜，24k星证实力。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "modelscope/FunClip",
     "url": "https://github.com/modelscope/FunClip", "desc": "按关键词自动剪辑视频（Windows可用）",
     "lang": "", "week": "", "stars_total": "6175", "win": "可用",
     "zh_desc": "阿里通义实验室开源的AI视频剪辑工具，基于FunASR语音识别：自动给视频生成字幕，你只需输入想保留的关键词或人名，它就能自动识别对应片段并一键剪出成片，支持中英文等语言。Windows下pip安装即可使用，做短视频、纪录片剪辑素材整理非常顺手。",
     "reason": "按关键词自动剪视频，口播号做素材整理的效率神器，Windows友好且上手极快。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "2noise/ChatTTS",
     "url": "https://github.com/2noise/ChatTTS", "desc": "会聊天、带情绪和笑声的对话式TTS",
     "lang": "", "week": "", "stars_total": "39789",
     "zh_desc": "专为日常对话场景设计的生成式语音模型，能输出自然、有情感起伏、带笑声和语气词的真人感语音，支持中英文混合与多说话人，比传统TTS更接近真人聊天。39k+星标，被广泛用于语音陪伴、播客、短视频配音等场景，本地即可部署。",
     "reason": "最像真人聊天的开源TTS，情绪和语气词让配音有灵魂，内容创作者必备神器。"},
    {"platform": "精选", "section": "github", "subgroup": "novel", "title": "OpenBMB/MiniCPM-V",
     "url": "https://github.com/OpenBMB/MiniCPM-V", "desc": "手机端侧能跑的多模态大模型",
     "lang": "", "week": "", "stars_total": "26219",
     "zh_desc": "面壁智能推出的口袋级端侧多模态模型系列，几十亿参数就能在图像、视频、音频理解上逼近GPT-4V等大模型，可在手机、平板等端侧设备离线运行，支持图像问答、视频理解、OCR等任务，26k+星标，让普通小设备也能拥有大模型的眼睛。",
     "reason": "手机端多模态理解天花板，与手机能跑的LLM主题完美呼应，端侧智能落地典范。"},
]


def fetch_curated_fun():
    """固定注入精选有趣项目（不参与 GitHub 池策展，直接进 RAW_ITEMS）。
    win 规则（2026-08-24）：精选项目必须 Windows 可用；未显式标注的默认"可用"。"""
    for it in CURATED_FUN:
        it.setdefault("win", "可用")
    RAW_ITEMS.extend(CURATED_FUN)
    return len(CURATED_FUN)


# ---------------------------------------------------------------- Provider 注册表
# 新增数据源：写一个 fetch_xxx() 返回条数，然后在这里注册一行即可。
SOURCES = [
    ("curated_fun", fetch_curated_fun, "精选有趣项目×20"),   # 固定注入，手写中文简介
    ("github", fetch_github_pool, "池→排除→策展"),   # 特殊：需要两步，见 main 处理
    ("hn", fetch_hn_ai, "Hacker News"),
    ("v2ex", fetch_v2ex_ai, "V2EX"),
    ("exa_ai", fetch_exa_ai, "Exa AI"),
    ("bili_ai", fetch_bili_ai, "B站 AI"),
    ("bili_doc", fetch_bilibili_doc, "B站 纪录片"),
    ("exa_doc", fetch_exa_doc, "Exa 纪录片"),
]


def run_source(name, fn, *a):
    """包一层：记录条数/错误/耗时进 STATS。"""
    t0 = time.time()
    try:
        n = fn(*a) or 0
        STATS["sources"][name] = {"got": n, "err": None,
                                  "ms": int((time.time() - t0) * 1000)}
        return n
    except Exception as e:
        STATS["sources"][name] = {"got": 0, "err": str(e)[:200],
                                  "ms": int((time.time() - t0) * 1000)}
        log(f"  ERR {name}: {e}")
        return 0


# ---------------------------------------------------------------- DeepSeek 中文简介
DS_PROMPT = (
    "你是中文科技内容编辑。为下面的条目撰写：\n"
    "1. zh_desc：≥80字的中文简介，讲清它是什么、解决什么问题、亮点是什么；\n"
    "2. reason：≥20字的收录理由，说明为什么值得关注（热度/新颖性/实用价值）；\n"
    "3. win：判断该项目在 Windows 上能否使用，只能取 \"可用\"（Windows原生可跑）/\"需WSL\"（需WSL或Docker）/\"不可用\"（无法在Windows运行，如仅Linux CUDA训练、iOS/macOS专属）。若条目是纪录片/视频/新闻资讯类内容（非软件项目），win 一律填 \"可用\"。\n"
    "只输出 JSON：{\"zh_desc\": \"...\", \"reason\": \"...\", \"win\": \"可用\"}\n\n"
    "标题：{title}\n简介：{desc}\n链接：{url}"
)


def call_deepseek(item):
    """单条生成 zh_desc+reason；成功返回 (zh_desc, reason)，失败返回 None。"""
    key = load_env_key("DEEPSEEK_API_KEY")
    if not key:
        log("  ERR 无 DEEPSEEK_API_KEY")
        return None
    body = json.dumps({
        "model": DS_MODEL,
        "messages": [{"role": "user", "content": DS_PROMPT
                      .replace("{title}", str(item.get("title", ""))[:200])
                      .replace("{desc}", str(item.get("desc", ""))[:400])
                      .replace("{url}", str(item.get("url", "")))}],
        "max_tokens": CONFIG["deepseek"]["max_tokens"],
        "temperature": CONFIG["deepseek"]["temperature"],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    last_err = None
    for attempt in range(CONFIG["deepseek"]["retries"]):
        try:
            req = urllib.request.Request(DS_URL, data=body, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            usage = data.get("usage") or {}
            STATS["deepseek"]["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
            STATS["deepseek"]["completion_tokens"] += int(usage.get("completion_tokens", 0))
            content = data["choices"][0]["message"]["content"]
            obj = json.loads(content)
            zh = str(obj.get("zh_desc", "")).strip()
            rn = str(obj.get("reason", "")).strip()
            win = str(obj.get("win", "可用")).strip()
            if win not in ("可用", "需WSL", "不可用"):
                win = "可用"
            if len(zh) >= 40 and len(rn) >= 20:
                return zh, rn, win
            last_err = f"内容不合格 len(zh)={len(zh)}"
        except Exception as e:
            last_err = str(e)[:120]
            time.sleep(2 * (attempt + 1))
    log(f"  ERR zh[{item.get('title', '')[:24]}...]: {last_err}")
    return None


def enrich_zh(items, workers=None):
    """为缺 zh_desc 的条目批量生成；原地更新 dict。返回成功条数。
    同时回填 win（Windows 可用性，用户规则 2026-08-24）。"""
    workers = workers or CONFIG["deepseek"]["workers"]
    todo = [it for it in items if not it.get("zh_desc")]
    if not todo:
        return 0
    STATS["deepseek"]["todo"] = len(todo)
    log(f"[中文简介] 待生成 {len(todo)} 条 → {DS_MODEL}（{workers} 并发）")
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(call_deepseek, it): it for it in todo}
        for f in as_completed(futs):
            it = futs[f]
            res = f.result()
            if res:
                it["zh_desc"] = res[0]
                it["win"] = res[2]
                if not it.get("reason") or len(str(it.get("reason", ""))) < 20:
                    # 收录理由 <20字 也重新生成（用户要求理由≥20字；GitHub 策展短句会被覆盖）
                    it["reason"] = res[1]
                ok += 1
            if ok and ok % 20 == 0:
                log(f"  ...已生成 {ok}/{len(todo)}")
    STATS["deepseek"]["ok"] = ok
    STATS["deepseek"]["failed"] = len(todo) - ok
    pi, po = (CONFIG["deepseek"].get("price_input_per_1m", 0.0),
              CONFIG["deepseek"].get("price_output_per_1m", 0.0))
    STATS["deepseek"]["cost_est_rmb"] = round(
        (STATS["deepseek"]["prompt_tokens"] * pi +
         STATS["deepseek"]["completion_tokens"] * po) / 1e6, 4)
    log(f"[中文简介] 完成 {ok}/{len(todo)}（失败 {len(todo) - ok} 条降级用原文）")
    return ok


# ---------------------------------------------------------------- HTML 生成
def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def card_html(items):
    if not items:
        return '<div class="empty">本区暂无条目</div>'
    out = []
    for it in items:
        src = esc(it.get("platform", ""))
        badge = ""
        if it.get("week"):
            badge = f'<span class="src hot">周★ +{esc(it.get("week"))}</span>'
        if it.get("win") == "需WSL":
            badge += '<span class="src wsl">🐧 WSL/Docker</span>'
        desc = it.get("zh_desc") or it.get("desc") or ""
        reason = it.get("reason")
        extra = f'<div class="card-desc">{esc(desc)}</div>'
        if reason:
            extra += f'<div class="card-reason">📌 收录理由：{esc(reason)}</div>'
        out.append(
            f'<div class="card"><div class="card-head"><span class="src">{src}</span>{badge}'
            f'<a class="card-title" href="{esc(it.get("url", ""))}" target="_blank">'
            f'{esc(it.get("title", ""))}</a></div>'
            f'{extra}</div>')
    return f'<div class="cards">{"".join(out)}</div>'


def build_html():
    gh = [it for it in RAW_ITEMS if it.get("section") == "github"]
    gh_novel = [it for it in gh if it.get("subgroup") == "novel"]
    gh_work = [it for it in gh if it.get("subgroup") == "work"]
    ai = [it for it in RAW_ITEMS if it.get("section") == "ai"]
    doc = [it for it in RAW_ITEMS if it.get("section") == "doc"]
    total = len(gh) + len(ai) + len(doc)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🧠 AutoAI 每日 AI 看板 — {TODAY}</title>
<style>
 body{{font-family:'Microsoft YaHei',system-ui,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}}
 .wrap{{max-width:980px;margin:0 auto}}
 h1{{font-size:22px;margin:0 0 4px}}
 .sub{{color:#8b93a7;font-size:13px;margin-bottom:18px}}
 h2{{font-size:17px;margin:28px 0 12px;padding:8px 12px;border-radius:8px}}
 h2.g{{background:#1a2332;border-left:4px solid #f78166}}
 h2.a{{background:#1a2332;border-left:4px solid #4ea1ff}}
 h2.d{{background:#1a2332;border-left:4px solid #ffcf5c}}
 h3{{font-size:14px;color:#f78166;margin:16px 0 8px}}
 .stat{{color:#8b93a7;font-size:12px;font-weight:normal;float:right}}
 a{{color:#4ea1ff;text-decoration:none}} a:hover{{text-decoration:underline}}
 .card{{background:#161b26;border:1px solid #232a38;border-radius:10px;padding:10px 14px;width:calc(50% - 5px);box-sizing:border-box}}
.cards{{display:flex;flex-wrap:wrap;gap:10px}}
 .card-head{{margin-bottom:4px}}
 .src{{display:inline-block;background:#232a38;color:#8b93a7;font-size:11px;border-radius:4px;padding:1px 7px;margin-right:8px}}
 .src.hot{{background:#2a2118;color:#f78166}}
 .src.wsl{{background:#2a2418;color:#ffcf5c}}
 .card-title{{font-size:14px;color:#e6e6e6;font-weight:bold}}
 .card-desc{{color:#8b93a7;font-size:12px;line-height:1.6}}
 .card-reason{{color:#ffcf5c;font-size:12px;line-height:1.5;margin-top:4px}}
 .empty{{color:#5a6272;font-size:13px;padding:10px}}
 .foot{{color:#5a6272;font-size:11px;margin-top:26px;text-align:center}}
</style></head><body><div class="wrap">
<h1>🧠 AutoAI 每日 AI 看板</h1>
<div class="sub">{TODAY} · 全平台自动采集 · 中文简介由 DeepSeek 生成 · 共 {total} 条</div>

<h2 class="g">🔥 GitHub 一周星标涨幅精选 <span class="stat">{len(gh)} 条 · 候选池 → DeepSeek 策展</span></h2>
<h3>🆕 新奇 / 有趣（{len(gh_novel)}）</h3>
{card_html(gh_novel)}
<h3>💼 工作 / 兴趣相关（{len(gh_work)}）</h3>
{card_html(gh_work)}

<h2 class="a">🧠 AI 前沿动态 <span class="stat">{len(ai)} 条 · HN + V2EX + Exa + B站</span></h2>
{card_html(ai)}

<h2 class="d">🎬 纪录片 &amp; 解说素材 <span class="stat">{len(doc)} 条 · B站热门 + Exa</span></h2>
{card_html(doc)}

<div class="foot">AutoAI 每日看板 · 数据源 GitHub API / HN / V2EX / Exa / B站 · 中文简介 {DS_MODEL}</div>
</div></body></html>"""


# ---------------------------------------------------------------- RSS 2.0 输出
def build_rss():
    from email.utils import format_datetime
    pub = format_datetime(datetime.now())
    items = []
    for it in RAW_ITEMS:
        desc = it.get("zh_desc") or it.get("desc") or ""
        reason = it.get("reason")
        if reason:
            desc = f"{desc}（📌 收录理由：{reason}）"
        items.append(
            f"<item><title>{esc(it.get('title', ''))}</title>"
            f"<link>{esc(it.get('url', ''))}</link>"
            f"<guid isPermaLink=\"false\">{esc(dedup_key_of(it))}</guid>"
            f"<description>{esc(desc)}</description>"
            f"<pubDate>{pub}</pubDate></item>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>🧠 AutoAI 每日 AI 看板 — {TODAY}</title>
<link>https://github.com/DaBaoAgent/autoai</link>
<description>AutoAI 每日 AI 看板：GitHub 精选 / AI 前沿动态 / 纪录片素材，中文简介由 DeepSeek 生成。</description>
<language>zh-CN</language>
<pubDate>{pub}</pubDate>
{''.join(items)}
</channel>
</rss>"""


def write_stats():
    STATS["finished"] = datetime.now().isoformat(timespec="seconds")
    STATS["duration_s"] = round(
        (datetime.fromisoformat(STATS["finished"]) -
         datetime.fromisoformat(STATS["started"])).total_seconds(), 1)
    STATS["total"] = len(RAW_ITEMS)
    try:
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(STATS, f, ensure_ascii=False, indent=1)
        log(f"STATS: {STATS_PATH}")
    except Exception as e:
        log(f"  ERR stats 写入: {e}")


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-enrich", action="store_true", help="只采集，不调 LLM 生成中文简介")
    ap.add_argument("--force", action="store_true", help="强制重采当日（忽略幂等复用）")
    ap.add_argument("--use-trending", action="store_true",
                    help="GitHub 用 trending 正则通道（默认 Search API）")
    args = ap.parse_args()
    if args.use_trending:
        CONFIG["github"]["use_trending_parse"] = True

    conn = init_db()
    log(f"=== AutoAI 每日 AI 看板 {TODAY} (v4) ===")

    # 幂等复用：当日已有数据 → 跳过采集/策展/LLM
    if not args.force:
        RAW_ITEMS.extend(load_today_from_db(conn) or [])

    if not RAW_ITEMS:
        log("[1/4] GitHub 精选（池→排除→策展）...")
        pool = fetch_github_pool()
        if pool:
            exclude = load_exclude(conn)
            log(f"  排除已推荐 {len(exclude)} 条")
            curated, pool_map = curate_github(pool, exclude)
            build_github_section(curated, pool_map)
        else:
            log("  ⚠️ GitHub 池为空，跳过 GitHub 区")

        log("[2/4] AI 前沿动态...")
        run_source("curated_fun", fetch_curated_fun)   # 精选有趣项目×20（固定注入，进 GitHub 区）
        run_source("hn", fetch_hn_ai)
        run_source("v2ex", fetch_v2ex_ai)
        run_source("exa_ai", fetch_exa_ai)
        run_source("bili_ai", fetch_bili_ai)

        log("[3/4] 纪录片 & 解说素材...")
        run_source("bili_doc", fetch_bilibili_doc)
        run_source("exa_doc", fetch_exa_doc)

        log("[4/4] 写入文件...")
        if not args.skip_enrich:
            enrich_zh(RAW_ITEMS)
        # Windows 可用性过滤（用户规则 2026-08-24：开源项目/技能必须 Windows 能用）
        dropped = [it.get("title", "") for it in RAW_ITEMS
                   if it.get("section") in ("github", "ai") and it.get("win") == "不可用"]
        if dropped:
            RAW_ITEMS[:] = [it for it in RAW_ITEMS
                            if not (it.get("section") in ("github", "ai") and it.get("win") == "不可用")]
            STATS["dropped_no_win"] = dropped
            log(f"[Windows过滤] 剔除 {len(dropped)} 条不可用: {dropped[:5]}{' ...' if len(dropped) > 5 else ''}")
        with open(RAW_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": TODAY, "items": RAW_ITEMS}, f, ensure_ascii=False, indent=1)
    else:
        log("[1/4] 复用今日数据（跳过采集/策展/LLM）")

    # 持久化 + 产物
    n_new, n_upd = persist(conn, RAW_ITEMS)
    log(f"[DB] 新增 {n_new} 条，补全 {n_upd} 条（data/items.db）")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(build_html())
    with open(RSS_PATH, "w", encoding="utf-8") as f:
        f.write(build_rss())
    log(f"RSS : {RSS_PATH}")
    conn.close()

    # 复制一份到桌面（用户要求）
    try:
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desk, exist_ok=True)
        desk_dst = os.path.join(desk, f"AI看板_{TODAY}.html")
        shutil.copyfile(HTML_PATH, desk_dst)
        log(f"已复制到桌面: {desk_dst}")
    except Exception as e:
        log(f"  ERR 复制到桌面: {e}")

    gh = sum(1 for it in RAW_ITEMS if it.get("section") == "github")
    ai = sum(1 for it in RAW_ITEMS if it.get("section") == "ai")
    doc = sum(1 for it in RAW_ITEMS if it.get("section") == "doc")
    write_stats()
    log(f"=== 完成：GitHub {gh} 条 / AI {ai} 条 / 纪录片 {doc} 条 ===")
    log(f"RAW : {RAW_PATH}")
    log(f"HTML: {HTML_PATH}")
    return 0 if (gh + ai + doc) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
