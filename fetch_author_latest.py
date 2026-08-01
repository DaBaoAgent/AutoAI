"""curl 提取分享页作者 sec_uid + 抓主页最新视频（Playwright 拦截 aweme/post API）"""
import json, time, sys, os, re, urllib.request

WORKDIR = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36"

def get_sec_uid_via_curl(vid):
    """curl 分享页，正则提取 sec_uid"""
    req = urllib.request.Request(f"https://www.iesdouyin.com/share/video/{vid}/",
                                 headers={"User-Agent": UA, "Accept": "*/*"})
    html = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'ignore')
    # 找 sec_uid（HTML 里直接内嵌）
    m = re.search(r'sec_uid\\?"\s*:\s*\\?"([^"\\]+)', html)
    if m:
        return m.group(1)
    # 尝试从 _ROUTER_DATA JSON 里找
    m2 = re.search(r'window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>', html, re.S)
    if m2:
        s = m2.group(1)
        m3 = re.search(r'"sec_uid"\s*:\s*"([^"]+)"', s)
        if m3:
            return m3.group(1)
    # 兜底：全文找
    m4 = re.search(r'MS4wLjAB[A-Za-z0-9_-]{40,}', html)
    if m4:
        return m4.group(0)
    print(f"  no sec_uid in share page {vid}, len={len(html)}")
    return None

if __name__ == "__main__":
    vid = sys.argv[1]
    sec = get_sec_uid_via_curl(vid)
    print("sec_uid:", sec)
    if sec:
        # 用 Playwright 抓主页
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                                viewport={"width":1280,"height":900})
            pg = ctx.new_page()
            videos = []
            def on_response(resp):
                if "aweme/post" in resp.url:
                    try:
                        body = resp.json()
                        for a in (body.get("aweme_list") or []):
                            videos.append({"id": a.get("aweme_id"), "desc": (a.get("desc") or "")[:100],
                                           "create_time": a.get("create_time")})
                    except Exception:
                        pass
            pg.on("response", on_response)
            pg.goto(f"https://www.douyin.com/user/{sec}", wait_until="domcontentloaded", timeout=35000)
            time.sleep(5)
            for i in range(8):
                pg.mouse.wheel(0, 4000)
                time.sleep(2.5)
            time.sleep(2)
            seen = set(); uniq = [v for v in videos if not (v["id"] in seen or seen.add(v["id"]))]
            print(f"共 {len(uniq)} 个视频:")
            for v in uniq[:15]:
                import datetime
                ts = datetime.datetime.fromtimestamp(v["create_time"]).strftime("%m-%d") if v.get("create_time") else "?"
                print(f"  {ts} {v['id']} | {v['desc'][:60]}")
            out = os.path.join(WORKDIR, "data", "tmp", f"author_{vid}.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"sec_uid": sec, "videos": uniq, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                          f, ensure_ascii=False, indent=2)
            print(f"已保存 {out}")
            b.close()
