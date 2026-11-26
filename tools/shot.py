"""ateruyo.html を画面に出ないブラウザで開いて撮る。
python tools/shot.py <出力の名前> [幅x高さ] [待つ秒] [query]"""
import sys, pathlib
from playwright.sync_api import sync_playwright
root = pathlib.Path(__file__).resolve().parent.parent
name = sys.argv[1]; w, h = map(int, (sys.argv[2] if len(sys.argv) > 2 else "1280x720").split("x"))
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 6; query = sys.argv[4] if len(sys.argv) > 4 else ""
out = root / "work" / "shots"; out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    p = b.new_page(viewport={"width": w, "height": h})
    errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.on("console", lambda m: m.type == "error" and errs.append(m.text))
    p.on("console", lambda m: m.text.startswith("GAME ") and (out / f"{name}.json").write_text(m.text[5:], encoding="utf-8"))
    base = "http://127.0.0.1:8770/" if query.startswith("?live") else (root / "ateruyo.html").as_uri()
    p.goto(base + query)
    if wait < 0:
        p.set_default_timeout(int(-wait * 1000))
        p.wait_for_selector("#share, #retry", timeout=int(-wait * 1000))
        p.wait_for_timeout(500)
    else:
        p.wait_for_timeout(int(wait * 1000))
    p.screenshot(path=str(out / f"{name}.png"))
    print("errors:", errs)
    b.close()
