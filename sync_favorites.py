"""
AutoAI 增量收藏同步脚本
使用 Playwright + Chrome Profile 获取抖音新收藏，自动 ingest 入库。
运行: python sync_favorites.py
"""
import json, time, os, sys, subprocess
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

WORKDIR = r"D:\@kaifa\autoai"
VENV_PYTHON = os.path.join(WORKDIR, ".venv", "Scripts", "python.exe")
CHROME_BASE = r"C:\Users\xxx13\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE = os.path.join(CHROME_BASE, "Default")
SYNC_STATE_PATH = os.path.join(WORKDIR, "data", "sources", "sync_state.json")
NEW_FAVORITES_PATH = os.path.join(WORKDIR, "data", "sources", "douyin_favorites_new.json")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_existing_ids():
    """Get all douyin source_ids already in DB."""
    result = subprocess.run([
        VENV_PYTHON, "-c", """
import sqlite3, json
con = sqlite3.connect('data/index/knowledge.db')
rows = con.execute("SELECT source_id FROM documents WHERE source_type='douyin'").fetchall()
con.close()
print(json.dumps([r[0] for r in rows]))
"""], capture_output=True, text=True, cwd=WORKDIR, timeout=10)
    return set(json.loads(result.stdout.strip()))

def fetch_favorites_via_api(cookies_dict):
    """Try douyin collection API with cookies."""
    import urllib.request, urllib.error
    
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies_dict.items() if v)
    
    # Douyin collection list API
    url = "https://www.douyin.com/aweme/v1/web/aweme/listcollection/?count=50&cursor=0"
    
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie_str)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Referer", "https://www.douyin.com/user/self")
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            items = data.get("aweme_list", [])
            return [{
                "id": str(item.get("aweme_id")),
                "title": item.get("desc", ""),
                "url": f"https://www.douyin.com/video/{item.get('aweme_id')}"
            } for item in items if item.get("aweme_id")]
    except Exception as e:
        print(f"  API call failed: {e}")
        return None

def fetch_favorites_via_page(browser):
    """Playwright 拦截收藏API响应 + DOM兜底提取。
    返回 (items, login_wall): login_wall=True 表示抖音未登录，无法获取收藏。"""
    page = browser.new_page()
    intercepted = []

    def on_response(resp):
        if "listcollection" in resp.url or "aweme/favorite" in resp.url:
            try:
                body = resp.json()
                for a in (body.get("aweme_list") or []):
                    if a.get("aweme_id"):
                        intercepted.append({
                            "id": str(a["aweme_id"]),
                            "title": a.get("desc", ""),
                            "url": f"https://www.douyin.com/video/{a['aweme_id']}"
                        })
            except Exception:
                pass

    page.on("response", on_response)
    dom_items, login_wall = [], False
    try:
        # douyin 首页实测约 38s 才渲染完，30s 超时太紧
        page.goto("https://www.douyin.com/user/self?showTab=favorite_collection",
                  wait_until="domcontentloaded", timeout=90000)
        time.sleep(8)
        # 滚动触发懒加载
        for _ in range(6):
            page.mouse.wheel(0, 3000)
            time.sleep(1.5)
        # 检测登录墙：未登录时收藏页只显示登录引导
        try:
            text = page.evaluate("() => document.body ? document.body.innerText : ''")
            login_wall = ("未登录" in text) or ("登录后即可观看" in text)
        except Exception:
            pass

        dom_items = page.evaluate("""() => {
            const results = [];
            // Try ROUTER_DATA first
            if (window._ROUTER_DATA) {
                try {
                    const data = window._ROUTER_DATA;
                    // Search for aweme_list in any key
                    for (const key of Object.keys(data.loaderData || {})) {
                        const items = data.loaderData[key]?.videoInfoRes?.item_list;
                        if (items && Array.isArray(items)) {
                            for (const item of items) {
                                results.push({
                                    id: String(item.aweme_id),
                                    title: item.desc || '',
                                    url: 'https://www.douyin.com/video/' + item.aweme_id
                                });
                            }
                        }
                    }
                } catch(e) {}
            }
            // Fallback: scrape video links from page
            if (results.length === 0) {
                const links = document.querySelectorAll('a[href*="/video/"]');
                const seen = new Set();
                links.forEach(l => {
                    const m = l.href.match(/video\\/(\\d+)/);
                    if (m && !seen.has(m[1])) {
                        seen.add(m[1]);
                        results.push({id: m[1], title: '', url: l.href});
                    }
                });
            }
            return results;
        }""")
    except Exception as e:
        print(f"  页面提取错误: {e}")
    finally:
        page.close()

    # 合并去重：拦截到的 API 数据优先，DOM 兜底
    seen, merged = set(), []
    for it in intercepted + (dom_items or []):
        if it["id"] not in seen:
            seen.add(it["id"])
            merged.append(it)
    return merged, login_wall

def main():
    print(f"[{now_iso()}] AutoAI 增量同步开始")
    
    # Get existing IDs
    existing = get_existing_ids()
    print(f"  已有收藏: {len(existing)} 条")
    
    # Launch Playwright with Chrome profile
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=CHROME_PROFILE,
            headless=True,
            args=['--disable-blink-features=AutomationControlled'],
            viewport={"width": 1280, "height": 800}
        )
        
        # Extract cookies for API calls
        cookies = browser.cookies()
        cookies_dict = {c['name']: c['value'] for c in cookies if 'douyin' in c.get('domain', '')}
        print(f"  获取到 {len(cookies_dict)} 个抖音cookie")
        
        # Try API approach first (faster, less prone to rendering issues)
        new_items = fetch_favorites_via_api(cookies_dict)
        
        if new_items is None:
            # API failed, try page-based approach (response interception + DOM fallback)
            print("  API方式失败，尝试浏览器拦截提取...")
            new_items, login_wall = fetch_favorites_via_page(browser)
            print(f"  浏览器提取到 {len(new_items)} 条"
                  + ("（⚠️ 检测到未登录，收藏页为登录引导页）" if login_wall else ""))
            if login_wall and not new_items:
                browser.close()
                state = {"last_sync": now_iso(), "total": len(existing), "new": 0,
                         "status": "login_required",
                         "hint": "Chrome无有效抖音登录态(sessionid)，请在浏览器登录 douyin.com 后重试"}
                os.makedirs(os.path.dirname(SYNC_STATE_PATH), exist_ok=True)
                with open(SYNC_STATE_PATH, 'w') as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)
                print(f"  [{now_iso()}] 同步中止：抖音未登录（status=login_required）")
                return state
        
        browser.close()
    
    # Filter new items
    new_items = new_items or []
    truly_new = [i for i in new_items if i['id'] not in existing]
    print(f"  本次新增: {len(truly_new)} 条 (总共提取 {len(new_items)} 条)")
    
    if not truly_new:
        print("  无新收藏，跳过ingest")
        # Update sync state
        state = {"last_sync": now_iso(), "total": len(existing), "new": 0, "status": "ok"}
        os.makedirs(os.path.dirname(SYNC_STATE_PATH), exist_ok=True)
        with open(SYNC_STATE_PATH, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return state
    
    # Save new favorites
    payload = {
        "account": {"name": "徐艾伦 Alan", "douyin_id": "tielanhai"},
        "collected_at": now_iso(),
        "count": len(truly_new),
        "items": truly_new
    }
    os.makedirs(os.path.dirname(NEW_FAVORITES_PATH), exist_ok=True)
    with open(NEW_FAVORITES_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {NEW_FAVORITES_PATH}")
    
    # Ingest
    result = subprocess.run(
        [VENV_PYTHON, "-m", "dabo_kb.cli", "ingest", NEW_FAVORITES_PATH],
        capture_output=True, text=True, cwd=WORKDIR, timeout=30
    )
    print(f"  Ingest: {result.stdout.strip()}")
    
    # Process new items
    if truly_new:
        print(f"  开始处理 {len(truly_new)} 条新收藏...")
        subprocess.run(
            [VENV_PYTHON, "playwright_fetch.py"],
            capture_output=True, text=True, cwd=WORKDIR, timeout=600
        )
        subprocess.run(
            [VENV_PYTHON, "-m", "dabo_kb.cli", "download-media", "--limit", str(len(truly_new))],
            capture_output=True, text=True, cwd=WORKDIR, timeout=300
        )
        subprocess.run(
            [VENV_PYTHON, "-m", "dabo_kb.cli", "transcribe-smart", "--limit", str(len(truly_new)), "--parallel-workers", "1"],
            capture_output=True, text=True, cwd=WORKDIR, timeout=900
        )
    
    # Update state
    state = {"last_sync": now_iso(), "total": len(existing) + len(truly_new), "new": len(truly_new)}
    os.makedirs(os.path.dirname(SYNC_STATE_PATH), exist_ok=True)
    with open(SYNC_STATE_PATH, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    print(f"[{now_iso()}] 同步完成: +{len(truly_new)} 条")
    return state

if __name__ == "__main__":
    main()
