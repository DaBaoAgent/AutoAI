#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量验证候选仓库详情：描述/topics/homepage，判断 Windows 可用性。"""
import json
import time
import urllib.parse
import urllib.request

CAND = [
    "obra/superpowers", "koala73/worldmonitor", "karpathy/autoresearch",
    "alchaincyf/nuwa-skill", "ayghri/i-have-adhd", "Leonxlnx/taste-skill",
    "block/buzz", "heygen-com/hyperframes", "HKUDS/ViMax", "ArcReel/ArcReel",
    "Huanshere/VideoLingo", "RVC-Boss/GPT-SoVITS", "debpalash/VoiceStudio",
    "DrewThomasson/ebook2audiobook", "vikiboss/60s", "HKUDS/CLI-Anything",
    "LaoFeng-mouse/flyingmouse-format", "JustVugg/colibri",
    "ultraworkers/claw-code", "calesthio/OpenMontage", "ATH-MaaS/Pixelle-Video",
    "dramaclaw/dramaclaw", "op7418/guizang-ppt-skill", "nexu-io/html-video",
    "msitarzewski/agency-agents", "emilkowalski/skills", "waooAI/waoowaoo",
    "zhukunpenglinyutong/desktop-cc-gui",
]

PROXY = "http://127.0.0.1:15715"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def fetch(url, timeout=25):
    for proxy in (PROXY, None):
        try:
            h = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.build_opener(h).open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
    return None

for repo in CAND:
    d = fetch(f"https://api.github.com/repos/{urllib.parse.quote(repo)}")
    if not d:
        print(f"ERR {repo}")
        continue
    print(f"== {repo} | {d.get('stargazers_count', 0)}★ | lang={d.get('language')} | created={d.get('created_at','')[:10]}")
    print(f"   desc: {(d.get('description') or '')[:220]}")
    print(f"   topics: {', '.join((d.get('topics') or [])[:12])}")
    print(f"   homepage: {d.get('homepage') or ''}")
    time.sleep(0.5)
