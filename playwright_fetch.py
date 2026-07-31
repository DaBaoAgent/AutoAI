"""
Playwright-based douyin share page fetcher.
Bypasses curl rate limiting by using a real browser.
Run: python playwright_fetch.py
"""
import json, time, sys, os
from datetime import datetime
from playwright.sync_api import sync_playwright

WORKDIR = os.path.dirname(os.path.abspath(__file__))
CANDIDATES_PATH = os.path.join(WORKDIR, "data", "tmp", "douyin_media_candidates.json")

def get_remaining_ids():
    import sqlite3
    db_path = os.path.join(WORKDIR, "data", "index", "knowledge.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT source_id FROM documents WHERE is_ai=1 AND status!='transcribed' ORDER BY source_id"
    ).fetchall()
    con.close()
    return [r['source_id'] for r in rows]

def main():
    ids = get_remaining_ids()
    print(f"Remaining: {len(ids)} items")
    
    candidates = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844}
        )
        
        for i, vid in enumerate(ids):
            url = f"https://www.iesdouyin.com/share/video/{vid}/"
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                html = page.content()
                
                if "window._ROUTER_DATA" in html:
                    start = html.find("window._ROUTER_DATA = ") + len("window._ROUTER_DATA = ")
                    end = html.find("</script>", start)
                    raw = html[start:end].strip().rstrip(";").strip()
                    data = json.loads(raw)
                    
                    items_list = data.get("loaderData", {}).get("video_(id)/page", {}).get("videoInfoRes", {}).get("item_list", [])
                    if items_list:
                        item = items_list[0]
                        play_addr = item.get("video", {}).get("play_addr", {})
                        video_urls = play_addr.get("url_list", []) if isinstance(play_addr, dict) else []
                        
                        candidates.append({
                            "id": vid,
                            "title": item.get("desc", ""),
                            "duration_ms": item.get("video", {}).get("duration"),
                            "audio_urls": [],
                            "video_urls": video_urls,
                        })
                        print(f"[{i+1}/{len(ids)}] OK {vid}: {item.get('desc','')[:30]}")
                
                page.close()
                time.sleep(2 + (i % 3))
                
            except Exception as e:
                print(f"[{i+1}/{len(ids)}] FAIL {vid}: {e}")
                try: page.close()
                except: pass
        
        browser.close()
    
    # Save
    payload = {
        "schema_version": 1,
        "captured_at": datetime.now().isoformat(),
        "ephemeral": True,
        "contains_signed_urls": True,
        "source": "playwright_share_page",
        "items": candidates
    }
    os.makedirs(os.path.dirname(CANDIDATES_PATH), exist_ok=True)
    with open(CANDIDATES_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    
    print(f"\nSaved {len(candidates)} candidates to {CANDIDATES_PATH}")

if __name__ == "__main__":
    main()
