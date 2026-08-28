#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拉取 GitHub 近期新奇候选池，排除已推荐，输出候选 JSON。"""
import json
import os
import time
import urllib.parse
import urllib.request

REC = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", "recommended_history.json"), encoding="utf-8"))
REC_GH = {u.replace("https://github.com/", "").rstrip("/").lower() for u in REC if "github.com/" in u}

PROXY = "http://127.0.0.1:15715"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
GH_API = "https://api.github.com/search/repositories"

def fetch(url, timeout=25):
    last = None
    for proxy in (PROXY, None):
        try:
            h = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.build_opener(h).open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
    raise last

QUERIES = [
    # 近期新建/活跃 + 趣味向
    'created:>2026-04-01 stars:>150 pushed:>2026-07-01',
    'created:>2026-03-01 stars:>300',
    'pushed:>2026-07-20 stars:>500 created:>2025-10-01',
    # 关键词向新奇玩法
    'topic:fun stars:>200 pushed:>2026-06-01',
    'topic:game-ai stars:>100 pushed:>2026-06-01',
    'topic:music-ai stars:>150 pushed:>2026-06-01',
    'topic:voice-cloning stars:>200 pushed:>2026-06-01',
    'topic:video-generation stars:>300 pushed:>2026-06-01',
    'topic:desktop-app stars:>500 created:>2025-08-01',
]

pool = {}
for q in QUERIES:
    try:
        url = f"{GH_API}?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page=30"
        data = fetch(url)
        for r in data.get("items", []):
            name = r.get("full_name") or ""
            if not name:
                continue
            pool[name.lower()] = {
                "repo": name,
                "desc": (r.get("description") or "")[:200],
                "lang": r.get("language") or "",
                "stars": r.get("stargazers_count") or 0,
                "created": (r.get("created_at") or "")[:10],
                "pushed": (r.get("pushed_at") or "")[:10],
                "topics": r.get("topics") or [],
            }
        print(f"OK {q[:55]}: +{len(data.get('items', []))}")
    except Exception as e:
        print(f"ERR {q[:55]}: {e}")
    time.sleep(1.2)

new = [v for k, v in sorted(pool.items(), key=lambda kv: -kv[1]["stars"]) if k not in REC_GH]
print(f"\n候选池 {len(pool)}，排除已推荐后 {len(new)}")

# 排除明显不适合的：AI 大模型仓库本身、训练框架、老牌工具
BLOCK = ("llm", "gpt-", "deepseek", "qwen", "glm-", "diffusion-model", "training",
         "framework", "dataset", "benchmark", "awesome-", "docs", "template")
def boring(v):
    d = (v["desc"] + " " + " ".join(v["topics"])).lower()
    return any(b in d for b in ("llm", "大模型", "benchmark", "dataset", "awesome list"))

out = [v for v in new if not boring(v)][:80]
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "candidates.json"),
          "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"筛选后 {len(out)} 个候选 -> data/candidates.json")
for v in out[:40]:
    print(f"  {v['repo']:<45} {v['stars']:>7}★ {v['lang']:<12} {v['desc'][:70]}")
