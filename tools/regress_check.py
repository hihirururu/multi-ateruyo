"""2026-11-26 に直した不具合の回帰テスト（AIには何も送らない。仮の答えで動かす）。
python tools/regress_check.py
1. 禁止の質問を3回出し続けても、審判には送らず、表に理由を残して相手の手番になる
2. 名前の自動判定: 長音を区別する・2つ以上の枠に合う別名は審判に聞く・1つの枠にだけ合う別名は自動・位置ちがいは「別の枠」
3. Ctrl+Enter の連打でも受付・開局は1回ずつ。受付中に名前を変えたら、古い返答で始めない
4. 予想の並びが前の質問と同じでも、別の手がかりの質問は通す。同じ質問の出し直しは止める
5. 審判には決まらなかった名前だけを聞き、一度決めた名前は使い回す（2つの枠に合う名前は使い回さない）
6. 「別の枠の正解」の置き場所の消去法・記録の短い書き方・終盤の攻略法・「わからない」の偏り・残り1枠のひとこと
"""
import io
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
root = pathlib.Path(__file__).resolve().parent.parent
ok = True


def expect(name, cond):
    global ok
    ok = ok and bool(cond)
    print(("○" if cond else "×"), name)


def open_page(b, errs):
    p = b.new_page(viewport={"width": 1280, "height": 720})
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto((root / "ateruyo.html").as_uri())
    p.wait_for_selector("#names")
    p.wait_for_timeout(300)
    return p


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    errs = []

    # ---- 1. 禁止の質問
    p = open_page(b, errs)
    p.evaluate("""(() => {
      window.__c = { guess: 0, oracle: 0 };
      const orig = window.mockAI;
      window.mockAI = q => {
        if (q.startsWith("【解答者】")) { window.__c.guess++; return JSON.stringify({ action: "question", question: "身長170cm以上ですか？", expect: ["はい", "いいえ", "はい"], conf: "mid", say: "" }); }
        if (q.startsWith("【回答係】")) window.__c.oracle++;
        return orig(q);
      };
    })()""")
    p.fill("#names", "織田信長\n豊臣秀吉\n徳川家康")
    p.click("#go")
    p.wait_for_selector("#game:not(.hidden)")
    p.wait_for_function("G && G.ev.filter(e => e.kind === 'inv').length >= 1", timeout=30000)
    first = p.evaluate("({ c: window.__c, e: G.ev[0], turn: G.turn })")
    expect("1手番で解答者に3回聞き、審判は0回", first["c"]["guess"] >= 3 and first["c"]["oracle"] == 0)
    expect("表に無効と理由が残る", first["e"]["kind"] == "inv" and first["e"]["text"] == "身長170cm以上ですか？" and "身長" in first["e"]["reason"])
    expect("無効の行が表に出ている", p.locator("tr.inv").count() >= 1)
    p.wait_for_function("G.ev.some(e => e.kind === 'q' && e.text.includes('無効が3回'))", timeout=60000)
    r = p.evaluate("({ c: window.__c, used: G.ev.filter(e => e.kind === 'q').map(e => e.p) })")
    expect("無効が3回続くと1問ぶん使う（今までと同じ数え方）", len(r["used"]) >= 1)
    expect("その間も審判は0回", r["c"]["oracle"] == 0)
    p.evaluate("G.stop = true")
    p.close()

    # ---- 2. 名前の自動判定
    p = open_page(b, errs)
    res = p.evaluate("""async () => {
      let judge = 0, last = null;
      const orig = window.mockAI;
      window.mockAI = q => { if (q.startsWith("【判定係】")) { judge++; last = q; return JSON.stringify({ match: Array((q.match(/^[A-Z]{1,2}: 「/gm) || []).length).fill("-") }); } return orig(q); };
      const run = async (T, names) => { G = { T, n: T.length, ev: [], stop: false }; judge = 0; const m = (await judgeGuess(names)).marks; return { m, judge }; };
      const t = (name, aliases = []) => ({ name, aliases: [name, ...aliases] });
      return {
        mira: await run([t("ミラー"), t("犬")], ["ミラ", "犬"]),
        mira2: await run([t("ミラー"), t("犬")], ["ミラー", "犬"]),
        dup: await run([t("ジョージ・ワシントン", ["ワシントン"]), t("ブッカー・T・ワシントン", ["ワシントン"])], ["ワシントン", "ブッカー・T・ワシントン"]),
        uniq: await run([t("徳川家康", ["家康"]), t("織田信長", ["信長"])], ["家康", "信長"]),
        swap: await run([t("ミラー"), t("犬")], ["犬", "ミラー"]),
      };
    }""")
    expect("「ミラ」は「ミラー」の自動の○にならない（審判に聞く）", res["mira"]["m"][0] != "o" and res["mira"]["judge"] == 1)
    expect("「犬」は自動で○", res["mira"]["m"][1] == "o")
    expect("「ミラー」は自動で○（審判に聞かない）", res["mira2"]["m"] == ["o", "o"] and res["mira2"]["judge"] == 0)
    expect("2つの枠に合う別名「ワシントン」は審判に聞く", res["dup"]["judge"] == 1 and res["dup"]["m"][1] == "o")
    expect("1つの枠にだけ合う別名は自動で○", res["uniq"]["m"] == ["o", "o"] and res["uniq"]["judge"] == 0)
    expect("正しい名前の位置ちがいは自動で「別の枠」", res["swap"]["m"] == ["w", "w"] and res["swap"]["judge"] == 0)
    p.close()

    # ---- 3. 出題の二重実行
    HOLD = """(() => {
      window.__rec = 0; window.__start = 0; window.__release = null;
      const orig = window.mockAI;
      window.mockAI = q => {
        if (q.startsWith("【受付】")) { window.__rec++; const text = orig(q); return new Promise(r => { window.__release = () => r(text); }); }
        if (q.startsWith("【解答者】")) return new Promise(() => {});  // 開局後は進めない
        return orig(q);
      };
      const st = window.startGame; window.startGame = ns => { window.__start++; return st(ns); };
    })()"""
    p = open_page(b, errs)
    p.evaluate(HOLD)
    p.fill("#names", "織田信長\n豊臣秀吉\n徳川家康")
    for _ in range(4):
        p.press("#names", "Control+Enter")
    p.wait_for_function("typeof window.__release === 'function'", timeout=10000)
    for _ in range(3):
        p.press("#names", "Control+Enter")
    p.wait_for_timeout(500)
    expect("連打しても受付は1回", p.evaluate("window.__rec") == 1)
    p.evaluate("window.__release()")
    p.wait_for_selector("#game:not(.hidden)", timeout=10000)
    p.wait_for_timeout(500)
    expect("開局も1回", p.evaluate("window.__start") == 1 and p.evaluate("window.__rec") == 1)
    p.close()

    p = open_page(b, errs)
    p.evaluate(HOLD)
    p.fill("#names", "織田信長\n豊臣秀吉\n徳川家康")
    p.press("#names", "Control+Enter")
    p.wait_for_function("typeof window.__release === 'function'", timeout=10000)
    p.fill("#names", "織田信長\n豊臣秀吉\n明智光秀")
    p.evaluate("window.__release()")
    p.wait_for_timeout(1000)
    expect("受付中に名前を変えたら、古いお題で始めない", p.evaluate("window.__start") == 0 and p.is_hidden("#game"))
    expect("出題画面に、押し直してほしいと出る", "名前が変わった" in p.inner_text("body"))
    p.close()

    # ---- 4. 予想の並びが同じでも、別の手がかりなら通す
    p = open_page(b, errs)
    r = p.evaluate("""() => {
      const T = ["夏目漱石", "森鷗外", "シェイクスピア"].map(name => ({ name, aliases: [name] }));
      const g = { T, n: 3, ev: [{ p: "O", kind: "q", no: 1, text: "日本人ですか？", answers: ["はい", "はい", "いいえ"], facts: [], over: {} }], final: { O: false, S: false } };
      const d = question => ({ action: "question", question, expect: ["はい", "はい", "いいえ"] });
      return { writer: precheck(g, "S", d("作家ですか？")), flip: precheck(g, "S", { action: "question", question: "外国の人ですか？", expect: ["いいえ", "いいえ", "はい"] }), same: precheck(g, "S", d("日本人ですか？")), month: precheck(g, "S", { action: "question", question: "2月生まれですか？", expect: ["はい", "いいえ", "わからない"] }), tall: precheck(g, "S", { action: "question", question: "身長170cm以上ですか？", expect: ["はい", "いいえ", "わからない"] }) };
    }""")
    expect("国籍の後の「作家ですか？」（予想が同じ並び）は通る", r["writer"] == "")
    expect("裏返しの並びでも差し戻さない（言いかえは審判が無効にする）", r["flip"] == "")
    expect("同じ質問の出し直しは止まる", "同じ" in r["same"])
    expect("誕生月の質問は通る（2026-11-26）", r["month"] == "")
    expect("身長の質問は止まる", "身長" in r["tall"])
    p.close()


    # ---- 5. 審判に聞く名前を絞る・使い回す
    p = open_page(b, errs)
    r = p.evaluate("""async () => {
      const calls = [];
      const orig = window.mockAI;
      window.mockAI = q => { if (q.startsWith("【判定係】")) { calls.push(q); const k = (q.match(/^[A-Z]{1,2}: 「/gm) || []).length; return JSON.stringify({ match: Array(k).fill("-") }); } return orig(q); };
      const t = (name, aliases = []) => ({ name, aliases: [name, ...aliases] });
      G = { T: [t("徳川家康", ["家康"]), t("ミラー"), t("ジョージ・ワシントン", ["ワシントン"]), t("ブッカー・T・ワシントン", ["ワシントン"])], n: 4, ev: [], stop: false };
      const a = await judgeGuess(["家康", "ミラ", "ワシントン", "ブッカー・T・ワシントン"]);
      const first = calls.slice();
      const b2 = await judgeGuess(["ミラ", "家康", "ワシントン", "ミラー"]);
      return { a: a.marks, first, n2: calls.length - first.length, second: calls.slice(first.length), b: b2.marks };
    }""")
    expect("審判には決まらなかった2つの名前だけを聞く", len(r["first"]) == 1 and "B: 「ミラ」" in r["first"][0] and "C: 「ワシントン」" in r["first"][0] and "「家康」" not in r["first"][0] and "上の順に2個" in r["first"][0])
    expect("1回目の採点", r["a"] == ["o", "x", "x", "o"])
    expect("一度決めた「ミラ」は使い回し、2つの枠に合う「ワシントン」だけをまた聞く", r["n2"] == 1 and "「ミラ」" not in r["second"][0] and "「ワシントン」" in r["second"][0])
    expect("2回目の採点（ミラは×のまま・ミラーは位置ちがい）", r["b"][0] == "x" and r["b"][1] == "w" and r["b"][3] == "w")
    p.close()

    # ---- 6. 局面のひとことと依頼文
    p = open_page(b, errs)
    r = p.evaluate("""() => {
      const T = ["徳川家康", "豊臣秀吉", "織田信長"].map(name => ({ name, aliases: [name] }));
      const mk = ev => ({ T, n: 3, ev, max: 15, prompt: "この3人の偉人、だ～れだ？", final: { O: false, S: false }, advice: [] });
      const a = (p, names, marks, to) => ({ p, kind: "a", names, marks, to, say: "", theme: "" });
      const q = (p, no, text, answers) => ({ p, kind: "q", no, text, answers, facts: [], over: {} });
      const g1 = mk([a("O", ["豊臣秀吉", "徳川家康", "織田信長"], ["w", "w", "o"], [1, 0, 2])]);
      const g2 = mk([a("O", ["豊臣秀吉", "坂本龍馬", "西郷隆盛"], ["w", "x", "x"], [1, -1, -1])]);
      const g3 = mk([q("O", 1, "身長は高いほうでしたか？", ["わからない", "わからない", "はい"]), q("S", 1, "日本の人ですか？", ["はい", "どちらかというとはい", "いいえ"])]);
      const g4 = mk([a("O", ["徳川家康", "豊臣秀吉", "明智光秀"], ["o", "o", "x"], [0, 1, -1])]);
      const g5 = mk([q("O", 1, "亀ですか？", ["どちらでもない", "どちらでもない", "はい"])]);
      return { h1: placeHint(g1), h2: placeHint(g2), s3: stageHint(g3, "O"), p3: guesserPrompt(g3, "O"), s4: stageHint(g4, "S"), t4: thinkingFor(g4), t0: thinkingFor(g3), s5: stageHint(g5, "S") };
    }""")
    expect("消去法で置き場所が決まり、全部埋まると出る", "「豊臣秀吉」→B" in r["h1"] and "「徳川家康」→A" in r["h1"] and "全部埋まる" in r["h1"])
    expect("置き場所が2つ残るときは候補を並べる", "「豊臣秀吉」→BかC" in r["h2"] and "全部埋まる" not in r["h2"])
    expect("記録は記号で短く（A○ B△ C×）", "A？ B？ C○" in r["p3"] and "A○ B△ C×" in r["p3"] and "A:はい" not in r["p3"] and "答えの記号:" in r["p3"])
    expect("「わからない」が半分以上の質問を知らせる", "「身長は高いほうでしたか？」は" in r["s3"] and "別の種類で聞く" in r["s3"])
    expect("「どちらでもない」が半分以上の質問を知らせる", "人として聞く前提" in r["s5"])
    expect("残り1枠のひとこと", "○のない枠は C だけ" in r["s4"])
    expect("終盤は序盤の段落を外す", "1. 最初の" not in r["t4"] and "- ばらばら" not in r["t4"] and "■解答" in r["t4"] and "よく出るテーマ" in r["t4"])
    expect("序盤は攻略法を全部渡す", "1. 最初の" in r["t0"] and "- ばらばら" in r["t0"])
    p.close()

    expect("ページのエラーなし", not errs)
    for e in errs[:5]:
        print("  ", e)
    b.close()

print("ぜんぶ通った" if ok else "通らないものがある")
sys.exit(0 if ok else 1)
