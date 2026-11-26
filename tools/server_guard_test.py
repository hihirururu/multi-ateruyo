"""server.py の歯止めを、本物の claude を呼ばずに確かめる。
python tools/server_guard_test.py
- 上限の1つ手前から、先に動く分と待っている分を用意する。先の分が止められて上限に届いたら、待っている分は起動しない。
- ほかのサイトから 127.0.0.1 に送られた呼び出しでは、Claude を動かさない（Host・Origin・Content-Type）。
"""
import io
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

tmp = Path(tempfile.mkdtemp())
server.GUARD_FILE = tmp / "guard.json"
server.LOG_FILE = tmp / "log.jsonl"
server.BLOCK_DIR = tmp / "blocked"
server.LIMIT = threading.Semaphore(1)  # 枠を1つにして、2つ目を必ず待たせる
server.MIN_GAP = 0.0
server.claude_cmd = lambda: ["本物のclaudeは呼ばない"]


def _never(*a, **k):
    raise RuntimeError("本物の claude を起動しようとした")


server.run_hidden = _never  # 差し替えそこねても、本物は起動しない
server.subprocess.run = _never
now = time.time()
server.GUARD_FILE.write_text('{"hashes": {}, "times": [%f, %f]}' % (now - 10, now - 5), encoding="utf-8")  # 上限(3)の1つ手前

started = []
first_in = threading.Event()
release = threading.Event()


def fake_run(cmd, prompt):
    started.append(prompt)
    if prompt == "先の分":
        first_in.set()
        release.wait(5)
        return 1, "", "Our safeguards flagged this message"
    return 0, '{"ok": true}', ""


server.run_claude = fake_run
res = {}


def call(name):
    try:
        res[name] = server.ask(name, "claude-sonnet-5-5")
    except Exception as e:  # noqa: BLE001
        res[name] = e


t1 = threading.Thread(target=call, args=("先の分",)); t1.start()
first_in.wait(5)
t2 = threading.Thread(target=call, args=("待っている分",)); t2.start()
time.sleep(0.3)  # 待っている分が、枠の前で待つところまで進む（最初の確認は通っている）
release.set(); t1.join(5); t2.join(5)

ok = True
for name, cond in [
    ("先の分は止められた扱い", isinstance(res.get("先の分"), server.Blocked)),
    ("待っている分は送らずに断られた", isinstance(res.get("待っている分"), server.Blocked) and "止めています" in str(res.get("待っている分"))),
    ("claude を起動したのは先の分だけ", started == ["先の分"]),
]:
    ok = ok and cond
    print("○" if cond else "×", name)

# ---- ほかのサイトからの呼び出し
import http.client  # noqa: E402
import json  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

asked = []
server.ask = lambda prompt, model: asked.append(prompt) or '{"ok": true}'
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
server.PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def post(headers):
    c = http.client.HTTPConnection("127.0.0.1", server.PORT, timeout=5)
    c.request("POST", "/api/ai", body=json.dumps({"prompt": "テスト", "modelTier": "default"}), headers=headers)
    r = c.getresponse(); r.read(); c.close()
    return r.status


me = f"127.0.0.1:{server.PORT}"
for name, cond in [
    ("ゲームの画面からの呼び出しは通る", post({"Host": me, "Origin": f"http://{me}", "Content-Type": "application/json"}) == 200),
    ("ほかのサイト（Origin が違う）からは断る", post({"Host": me, "Origin": "https://evil.example", "Content-Type": "application/json"}) == 403),
    ("フォームのような text/plain の送信は断る", post({"Host": me, "Content-Type": "text/plain"}) == 403),
    ("別の名前（DNS の付け替え）からは断る", post({"Host": f"evil.example:{server.PORT}", "Content-Type": "application/json"}) == 403),
    ("断った呼び出しでは Claude を動かしていない", asked == ["テスト"]),
]:
    ok = ok and cond
    print("○" if cond else "×", name)
httpd.shutdown()
print("ぜんぶ通った" if ok else "通らないものがある")
sys.exit(0 if ok else 1)
