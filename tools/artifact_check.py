"""claude.ai に出す版（ateruyo_artifact.html）を、AIに何も送らずに確かめる。
python tools/artifact_check.py
claude.ai のページと同じ形の、にせもののAI（window.claude）と保存（window.storage）を入れて、画面に出ないブラウザで動かす。
- 絵が入っていない・写しちがいの点検が働く・歯止めの書き方の表が server.py と同じ
- 止められた手番は送り直さずパス、同じ文は二度と送らない、24時間の上限、1秒あける・同時に2つまで
撮った画面は work/shots/art_*.png
"""
import importlib.util
import io
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "tools"))
import build_artifact  # noqa: E402

out = root / "work" / "shots"
out.mkdir(parents=True, exist_ok=True)
ok = True


def expect(name, cond):
    global ok
    ok = ok and bool(cond)
    print(("○" if cond else "×"), name)


dst, html = build_artifact.build()
uri = dst.as_uri()

# にせもののAI。rules: 何番目の【解答者】の依頼をどう失敗させるか。「試し止…」「試し網…」で始まる文は、いつもその失敗をする
FAKE = """(() => {
window.__calls = []; window.__live = 0; window.__maxLive = 0; window.__n = 0;
window.__rules = %s; window.__st = %s;
const SG = "API Error: Sonnet 5.5's safeguards flagged this message";
window.claude = { use: async () => async (prompt, opts) => {
  window.__calls.push({ t: Date.now(), p: prompt, model: opts && opts.model });
  window.__live++; window.__maxLive = Math.max(window.__maxLive, window.__live);
  try {
    await new Promise(r => setTimeout(r, 150));
    let rule = prompt.startsWith("試し止") ? "throw_sg" : prompt.startsWith("試し網") ? "throw_net" : "";
    if (prompt.startsWith("【解答者】")) rule = window.__rules[++window.__n] || "";
    if (rule === "throw_sg") throw new Error(SG);
    if (rule === "throw_net") throw new Error("Failed to fetch");
    if (rule === "text_sg") return { text: SG };
    let text; try { text = await window.mockAI(prompt); } catch (e) { text = "{}"; }
    return { text, modelApplied: opts && opts.model };
  } finally { window.__live--; }
} };
window.storage = { get: async k => (k in window.__st ? { value: window.__st[k] } : null), set: async (k, v) => { window.__st[k] = v; } };
})()"""


def page(b, rules=None, store=None, size=(1280, 720), errs=None):
    ctx = b.new_context(viewport={"width": size[0], "height": size[1]})
    ctx.add_init_script(FAKE % (json.dumps(rules or {}), json.dumps(store or {})))
    p = ctx.new_page()
    if errs is not None:
        p.on("pageerror", lambda e: errs.append(str(e)))
    return p


def start(p, names, query="?max=2"):
    p.goto(uri + query)
    p.wait_for_selector("#names")
    p.fill("#names", "\n".join(names))
    p.click("#go")


def guessers(p):
    return [c["p"] for c in p.evaluate("window.__calls") if c["p"].startswith("【解答者】")]


def guard(p):
    return json.loads(p.evaluate("window.__st.guard || '{}'") or "{}")


# ---- 0. ファイルそのもの
spec = importlib.util.spec_from_file_location("srv", root / "server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
expect("絵のデータが入っていない", "data:image" not in html and "const STAND = {};" in html)
expect(f"大きさは120KBより小さい（{len(html.encode('utf-8')) // 1024}KB）", len(html.encode("utf-8")) < 120 * 1024)  # 2026-11-24: claude.ai は添付をそのまま使い、書き写さないと分かったので100KBから上げた

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    errs = []

    # ---- 1. ふつうの1局（にせもののAI）
    p = page(b, errs=errs)
    start(p, ["宮本武蔵", "大谷翔平"])
    p.wait_for_selector("#game:not(.hidden)")
    expect("書き方の表が server.py と同じ", p.evaluate("SHAPE_BAN.map(r => r.source)") == srv.SHAPE_BAN)
    expect("にせもののAIを claude.ai のページとして使っている", p.evaluate("ENV") == "artifact" and not p.evaluate("MOCK"))
    p.wait_for_timeout(9000)
    p.screenshot(path=str(out / "art_mid.png"))
    p.wait_for_selector("#share", timeout=180000)
    p.wait_for_timeout(300)
    p.screenshot(path=str(out / "art_end.png"))
    calls = p.evaluate("window.__calls")
    ts = [c["t"] for c in calls]
    gap = min(y - x for x, y in zip(ts, ts[1:]))
    expect(f"送るたびに1秒以上あけている（いちばん短い間 {gap}ミリ秒・{len(calls)}回）", gap >= 990)
    expect("同時に送るのは2つまで", p.evaluate("window.__maxLive") <= 2)
    expect("左下に MOCK・写しちがい が出ていない", p.evaluate("document.getElementById('dbg').textContent") == "")
    expect("Opus と Sonnet と審判を別々のモデルで呼んでいる", {c["model"] for c in calls} == {"claude-opus-5-5", "claude-sonnet-5-5"})
    st = p.evaluate("Object.keys(window.__st)")
    expect("攻略メモ・戦績・歯止めの記録を claude.ai の保存に書いている", {"lessonsO", "lessonsS", "history", "guard"} <= set(st))
    expect("止められていない", not guard(p)["t"] and not guard(p)["f"])
    p.context.close()

    # ---- 2. 止められた手番（エラーで返る／答えの文で返る）→ 送り直さずパス。2回で中止
    for rule, label in (("throw_sg", "エラーで返る"), ("text_sg", "答えの文で返る")):
        p = page(b, {2: rule}, errs=errs)
        start(p, ["宮本武蔵", "大谷翔平"])
        p.wait_for_selector("#share", timeout=180000)
        g = guessers(p)
        expect(f"止められた手番（{label}）はパスになる", p.locator("tr.pass").count() == 1)
        expect("止められた文と同じ文を送り直していない", g.count(g[1]) == 1)
        expect("止められた回数を記録している", len(guard(p)["t"]) == 1 and 1 in guard(p)["h"].values())
        expect("「続ける」のボタンは出ない", p.locator("#retry").count() == 0)
        p.context.close()
    p = page(b, {2: "throw_sg", 3: "throw_sg"}, errs=errs)
    start(p, ["宮本武蔵", "大谷翔平"])
    p.wait_for_selector("#res:not(.hidden)", timeout=180000)
    n1 = p.evaluate("window.__calls.length")
    p.wait_for_timeout(3000)
    expect("2回止められたら対局は中止", "中止" in p.inner_text("#res"))
    expect("中止のあとは何も送らない（感想戦も送らない）", n1 == p.evaluate("window.__calls.length") and not any(c["p"].startswith("【感想戦】") for c in p.evaluate("window.__calls")))
    p.screenshot(path=str(out / "art_abort.png"))
    p.context.close()

    # ---- 3. 理由の分からない失敗 → 自分からは送り直さずパス
    p = page(b, {2: "throw_net"}, errs=errs)
    start(p, ["宮本武蔵", "大谷翔平"])
    p.wait_for_selector("#share", timeout=180000)
    g = guessers(p)
    expect("理由の分からない失敗の手番もパスになる", p.locator("tr.pass").count() == 1)
    expect("同じ文を自分から送り直していない", g.count(g[1]) == 1)
    expect("失敗の回数を記録している（止められた回数には数えない）", len(guard(p)["f"]) == 1 and not guard(p)["t"])
    p.context.close()

    # ---- 4. 文を1つずつ送って、断り方を見る
    p = page(b, errs=errs)
    p.goto(uri)
    p.wait_for_selector("#names")
    TRY = "async t => { const n = window.__calls.length; try { await ai(t); return ['ok', window.__calls.length - n]; } catch (e) { return [e.blocked ? 'blocked' : 'error', window.__calls.length - n, e.message]; } }"
    r = p.evaluate(TRY, "■前の手番のメモ\n- Aは武将")
    expect("止められたことのある書き方は、送る前に断る", r[0] == "blocked" and r[1] == 0)
    r = p.evaluate(TRY, "■出力（JSONのみ。考えた過程は書かない）")
    expect("いまの書き方は通す", r[0] == "ok" and r[1] == 1)
    r1 = p.evaluate(TRY, "試し止1")
    r2 = p.evaluate(TRY, "試し止1")
    expect("止められた文と同じ文は二度と送らない", r1[:2] == ["blocked", 1] and r2[:2] == ["blocked", 0])
    r1 = p.evaluate(TRY, "試し網1")
    r2 = p.evaluate(TRY, "試し網1")
    expect("理由の分からない失敗をした文も、すぐには送り直さない", r1[:2] == ["blocked", 1] and r2[:2] == ["blocked", 0])
    p.evaluate(TRY, "試し止2")
    p.evaluate(TRY, "試し止3")
    r = p.evaluate(TRY, "まったく別の文")
    expect(f"24時間に3回止められたら、何も送らない（{r[2] if len(r) > 2 else ''}）", r[:2] == ["blocked", 0])
    p.fill("#names", "宮本武蔵\n大谷翔平")
    n = p.evaluate("window.__calls.length")
    p.click("#go")
    p.wait_for_selector("#smsg div")
    expect("上限のあとは、出題しても何も送らず、理由を出す", n == p.evaluate("window.__calls.length") and "送りません" in p.inner_text("#smsg"))
    p.screenshot(path=str(out / "art_cap.png"))
    saved = p.evaluate("window.__st")
    p.context.close()

    # ---- 5. 開き直しても上限は残る
    p = page(b, store=saved, errs=errs)
    p.goto(uri)
    p.wait_for_selector("#names")
    r = p.evaluate(TRY, "開き直した後の文")
    expect("ページを開き直しても、上限は残っている", r[:2] == ["blocked", 0])
    p.context.close()

    # ---- 6. 写しちがい
    bad = dst.with_name("_art_bad.html")
    bad.write_text(html.replace("参考にするかはあなたが決める", "参考にするかはあなたが決めろ", 1), encoding="utf-8", newline="\n")
    p = page(b, errs=errs)
    p.goto(bad.as_uri())
    p.wait_for_selector("#names")
    expect("1字変えたページは、左下に「写しちがい」と出る", "写しちがい" in p.evaluate("document.getElementById('dbg').textContent"))
    p.context.close()
    bad.unlink()
    ws = dst.with_name("_art_ws.html")
    ws.write_text(html.replace("\n", "\r\n").replace("  const ", "    const "), encoding="utf-8", newline="")
    p = page(b, errs=errs)
    p.goto(ws.as_uri())
    p.wait_for_selector("#names")
    expect("空白や改行だけが変わったページでは出ない", p.evaluate("document.getElementById('dbg').textContent") == "")
    p.context.close()
    ws.unlink()

    # ---- 7. 画面（人数の多いお題・せまい画面）
    p = page(b, errs=errs)
    p.goto(uri + "?demo=p15&max=3")
    p.wait_for_timeout(12000)
    p.screenshot(path=str(out / "art_many.png"))
    p.context.close()
    p = page(b, size=(390, 800), errs=errs)
    p.goto(uri + "?max=3")
    p.wait_for_timeout(1500)
    p.screenshot(path=str(out / "art_mob_setup.png"))
    p.fill("#names", "織田信長\n豊臣秀吉\n徳川家康")
    p.click("#go")
    p.wait_for_selector("#share", timeout=180000)
    p.wait_for_timeout(300)
    p.screenshot(path=str(out / "art_mob_end.png"))
    p.context.close()
    p = page(b, errs=errs)
    p.goto(uri)
    p.wait_for_timeout(1500)
    p.screenshot(path=str(out / "art_setup.png"))
    p.context.close()

    expect(f"ページのエラーなし {errs or ''}", not errs)
    b.close()

print("全部よし" if ok else "だめな所がある")
sys.exit(0 if ok else 1)
