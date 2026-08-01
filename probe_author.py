#!/usr/bin/env python3
"""抓取作者主页最新视频列表 — 移动端UA + 分享页方式"""
import json, re, sys, time, random
import urllib.request

UA_MOBILE = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Mobile Safari/537.36"

def fetch(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA_MOBILE, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode('utf-8', 'ignore')
        except Exception as e:
            print(f"  attempt {i+1} failed: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None

if __name__ == "__main__":
    # 从收藏 JSON 找 sec_uid 或作者主页线索
    d = json.load(open('data/sources/douyin_favorites_full_2026-07-30.json', encoding='utf-8'))
    acct = d.get('account', {})
    print("account:", acct)
    # 尝试从视频 URL 推断作者 sec_uid（分享页里通常有）
    # 先直接试分享页看能否拿到 _ROUTER_DATA 里的 author
    sample_ids = sys.argv[1:] or [d['items'][0]['id']]
    for vid in sample_ids:
        print(f"\n=== video {vid} ===")
        html = fetch(f"https://www.iesdouyin.com/share/video/{vid}/")
        if not html:
            print("  FAILED to fetch")
            continue
        m = re.search(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', html)
        if m:
            import urllib.parse
            data = json.loads(urllib.parse.unquote(m.group(1)))
            print("  RENDER_DATA found, keys:", list(data.keys())[:10] if isinstance(data, dict) else type(data))
            # 尝试找 author sec_uid
            s = json.dumps(data, ensure_ascii=False)
            for key in ['sec_uid', 'unique_id', 'nickname']:
                for mm in re.finditer(r'"%s"\s*:\s*"([^"]{4,60})"' % key, s):
                    print(f"  {key}: {mm.group(1)}")
                    break
        else:
            print("  no RENDER_DATA, len:", len(html))
