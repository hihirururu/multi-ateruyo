"""アテルーヨーの進行を、AIに何も送らずに確かめる（画面に出ないブラウザ・仮の答え）。
python tools/flow_check.py
- アドバイス欄に書いた文が、次の手番の依頼文と感想戦の依頼文に入ること
- AIに届かなかった手番は、送り直さずにパスになること。1局で2回あったら対局をやめ、そのあとは何も送らないこと
撮った画面は work/shots/flow_*.png
"""
import io
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
root = pathlib.Path(__file__).resolve().parent.parent
out = root / "work" / "shots"
out.mkdir(parents=True, exist_ok=True)
ok = True


def expect(name, cond):
    global ok
    ok = ok and bool(cond)
    print(("○" if cond else "×"), name)


HOOK = """(() => {
window.__calls = []; window.__failAt = %s; window.__n = 0;
const orig = window.mockAI;
window.mockAI = p => {
  window.__calls.push(p);
  if (p.startsWith("【解答者】")) { window.__n++; if (window.__failAt.includes(window.__n)) { const e = new Error("AIの安全確認に止められました。同じ文は送り直しません"); e.blocked = true; throw e; } }
  return orig(p);
};
})()"""


def start(page, names, fail):
    page.goto((root / "ateruyo.html").as_uri())
    page.wait_for_selector("#names")
    page.evaluate(HOOK % fail)
    page.fill("#names", "\n".join(names))
    page.click("#go")
    page.wait_for_selector("#game:not(.hidden)")


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    errs = []

    # ---- 1. アドバイス
    p = b.new_page(viewport={"width": 1280, "height": 720})
    p.on("pageerror", lambda e: errs.append(str(e)))
    start(p, ["織田信長", "豊臣秀吉", "徳川家康"], "[]")
    p.wait_for_selector("#advText")
    expect("対局中にアドバイス欄が出ている", p.is_visible("#advText"))
    p.fill("#advText", "性別は聞かなくていいよ")
    p.press("#advText", "Enter")
    expect("送ると欄が空になる", p.input_value("#advText") == "")
    p.wait_for_timeout(1500)
    p.screenshot(path=str(out / "flow_adv.png"))
    p.wait_for_selector("#share", timeout=120000)
    calls = p.evaluate("window.__calls")
    g = [c for c in calls if c.startswith("【解答者】")]
    r = [c for c in calls if c.startswith("【感想戦】")]
    expect("送った後の手番の依頼文にアドバイスが入る", any("性別は聞かなくていいよ" in c for c in g))
    expect("送る前の手番の依頼文には入っていない", "性別は聞かなくていいよ" not in g[0])
    expect("感想戦の依頼文にアドバイスが入る", r and all("性別は聞かなくていいよ" in c for c in r))
    p.screenshot(path=str(out / "flow_end.png"))
    p.close()

    # ---- 2. 1回届かなかった → パスして続く
    p = b.new_page(viewport={"width": 1280, "height": 720})
    p.on("pageerror", lambda e: errs.append(str(e)))
    start(p, ["宮本武蔵", "大谷翔平"], "[2]")
    p.wait_for_selector("#share", timeout=120000)
    calls = p.evaluate("window.__calls")
    g = [c for c in calls if c.startswith("【解答者】")]
    expect("届かなかった手番はパスの行になる", p.locator("tr.pass").count() == 1)
    expect("届かなかった依頼文と同じ文を送り直していない", g.count(g[1]) == 1)
    expect("パスのあとも対局が続いて終わる", "中止" not in p.inner_text("#res"))
    expect("「続ける」のボタンは出ない", p.locator("#retry").count() == 0)
    p.close()

    # ---- 3. 2回届かなかった → 対局をやめ、そのあと何も送らない
    p = b.new_page(viewport={"width": 1280, "height": 720})
    p.on("pageerror", lambda e: errs.append(str(e)))
    start(p, ["宮本武蔵", "大谷翔平"], "[2,3]")
    p.wait_for_selector("#res:not(.hidden)", timeout=120000)
    n1 = p.evaluate("window.__calls.length")
    p.wait_for_timeout(2500)
    n2 = p.evaluate("window.__calls.length")
    expect("2回届かなかったら対局は中止", "中止" in p.inner_text("#res"))
    expect("中止のあとは何も送らない（感想戦も送らない）", n1 == n2 and not any(c.startswith("【感想戦】") for c in p.evaluate("window.__calls")))
    p.screenshot(path=str(out / "flow_abort.png"))
    p.close()

    # ---- 4. 人数の多いお題の画面
    p = b.new_page(viewport={"width": 1280, "height": 720})
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto((root / "ateruyo.html").as_uri() + "?demo=p15")
    p.wait_for_timeout(7000)
    p.screenshot(path=str(out / "flow_many.png"))
    p.close()

    expect(f"ページのエラーなし {errs or ''}", not errs)
    b.close()

print("全部よし" if ok else "だめな所がある")
sys.exit(0 if ok else 1)
