#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从历史看板 HTML + items.db 提取已推荐项目清单，保存为 JSON。"""
import glob
import json
import os
import re
import sqlite3

BOARD_DIR = r"D:\BaiduSyncdisk\2 @AI编程\AI看板"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recommended_history.json")

recs = {}  # url -> {title, sources}

# 1. 从 HTML 提取卡片
pat = re.compile(r'<a class="card-title" href="([^"]+)"[^>]*>([^<]+)</a>', re.S)
for f in sorted(glob.glob(os.path.join(BOARD_DIR, "AI看板_*.html"))):
    try:
        html = open(f, encoding="utf-8").read()
    except Exception as e:
        print(f"skip {f}: {e}")
        continue
    for url, title in pat.findall(html):
        url = url.strip()
        if not url.startswith("http"):
            continue
        rec = recs.setdefault(url, {"title": title.strip(), "files": []})
        rec["files"].append(os.path.basename(f))

# 2. 从 items.db 提取 dedup_key + 标题
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "items.db")
if os.path.exists(db):
    try:
        conn = sqlite3.connect(db)
        for url, title in conn.execute("SELECT url, title FROM items"):
            if url:
                rec = recs.setdefault(url, {"title": title, "files": ["db"]})
                if "db" not in rec["files"]:
                    rec["files"].append("db")
        conn.close()
    except Exception as e:
        print(f"db err: {e}")

for r in recs.values():
    r["files"] = sorted(set(r["files"]))

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(recs, f, ensure_ascii=False, indent=1)

gh = [u for u in recs if "github.com/" in u]
print(f"历史已推荐 {len(recs)} 条 (github {len(gh)}) -> {OUT}")
# 打印 github 项目名供去重
for u in sorted(gh):
    print("  ", u.replace("https://github.com/", "").rstrip("/"))
