"""AIに何も送らずに、歯止め（server.py）と依頼文の形を確かめる。
python tools/guard_check.py
- server.py の SHAPE_BAN が、止められたことのある書き方を断り、いまの依頼文は通すこと
- 同じ文の送り直し・24時間の上限が効くこと（仮の記録ファイルで試す。本物の記録には触れない）
"""
import importlib.util
import io
import pathlib
import re
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
root = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("srv", root / "server.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ok = True


def expect(name, cond):
    global ok
    ok = ok and bool(cond)
    print(("○" if cond else "×"), name)


tmp = pathlib.Path(tempfile.mkdtemp())
m.GUARD_FILE, m.LOG_FILE, m.BLOCK_DIR = tmp / "g.json", tmp / "l.jsonl", tmp / "b"


def refused(text):
    try:
        m.check_before_send(text, "claude-sonnet-5-5")
        return False
    except m.Blocked:
        return True


# 1. いまのページの依頼文（部品）に、断られる形が入っていない
for f in ("ateruyo.html", "ateruyo_artifact.html"):
    html = (root / f).read_text(encoding="utf-8")
    a = html.find("プロンプト")
    b = html.find("モック", a)
    body = html[a:b] if a >= 0 and b > a else html
    hits = [pat for pat in m.SHAPE_BAN if re.search(pat, body, re.S)]
    expect(f"{f} の依頼文に、止められたことのある書き方が無い {hits or ''}", not hits)

# 2. 止められたことのある書き方は、送る前に断る
for t in ["■あなたの前の手番の謎解き（続きから考える。答えと合わなくなった候補は捨てる）", "■前の手番のメモ\n- Aは武将",
          '出力: {"think": "考えたこと", "action": "question"}', "考えた過程をすべて書いてから答える",
          "分かったこと: …\nテーマの候補: …\n方針: …"]:
    expect("断る: " + t.splitlines()[0][:30], refused(t))
for t in ["■出力（JSONのみ。考えた過程は書かない）", "【回答係】質問: 「政治家ですか？」", "■出題者（人間）からのアドバイス\n- 性別は聞かなくていい"]:
    expect("通す: " + t.splitlines()[0][:30], not refused(t))

# 3. 一度止められた文は二度と送らない／24時間に MAX_BLOCKS 回で止まる
h = m.check_before_send("試しの文1", "claude-sonnet-5-5")
m.note_block("試しの文1", "claude-sonnet-5-5", h, "safeguards flagged")
expect("止められた文と同じ文は送らない", refused("試しの文1"))
expect("別の文は送れる", not refused("試しの文2"))
for i in range(2, m.MAX_BLOCKS + 1):
    t = f"試しの文{i}"
    m.note_block(t, "claude-sonnet-5-5", m.check_before_send(t, "claude-sonnet-5-5"), "safeguards flagged")
expect(f"24時間に{m.MAX_BLOCKS}回止められたら、何も送らない", refused("まったく別の文"))
g = m._guard()
g["times"] = [time.time() - m.DAY - 10 for _ in g["times"]]
m.GUARD_FILE.write_text(__import__("json").dumps(g), encoding="utf-8")
expect("24時間たてば、また送れる（止められた文そのものは送らない）", not refused("まったく別の文") and refused("試しの文1"))

print("全部よし" if ok else "だめな所がある")
sys.exit(0 if ok else 1)
