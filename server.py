"""複数人アテルーヨーを手元で本物のAIつきで遊ぶためのサーバー。

ateruyo.html をそのまま配信し、ページからの AI 呼び出し（/api/ai）を Claude Code の
`claude -p`（ユーザーのClaudeのログインを使う）で答える。ファイルを直せば再読み込みだけで反映される。
起動: アテルーヨー.bat をダブルクリック（またはpython server.py）。終了: タスクバーにある最小化した窓を閉じる。claude は見えないデスクトップで動かす（窓が点滅しない）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8770  # もう一方のゲームと同じ番号（ブラウザの保存がこの番号ごとに分かれるので、変えると記録が見えなくなる）
GAME = "複数人アテルーヨー"
WORK = Path(tempfile.gettempdir()) / "ateruyo_ai_work"  # 空のフォルダで動かす（ファイルを読ませない）
TIER_MODEL = {"complex": "claude-opus-5-5", "default": "claude-sonnet-5-5", "quick": "claude-haiku-5-5"}
MODEL_TIER = {v: k for k, v in TIER_MODEL.items()}
SYSTEM = "あなたは日本語で遊ぶ推理ゲームの参加者です。指示された形式のJSONだけを出力します。"
LIMIT = threading.Semaphore(2)  # 同時に動かす claude は2つまで
MIN_GAP = 1.0  # 送信と送信の間は1秒以上あける

# ---- 止められる文を送らないための歯止め
# 1. 止められたことのある形（AIに考えた中身を書き出させる・前の手番の考えを渡す）の依頼文は、送らずに断る。
# 2. 一度止められた文は、二度と送らない（文のハッシュを work/ai_guard.json に覚える）。
# 3. 24時間に MAX_BLOCKS 回止められたら、その24時間はもう何も送らない。
# 4. 送った回数・止められた回数は work/ai_log.jsonl に残す（数は記録で数える。推測で言わない）。
# 止められた文は work/blocked/ に保存する。原因探しのために送り直さない。読んで書き直す。
GUARD_FILE = ROOT / "work" / "ai_guard.json"
LOG_FILE = ROOT / "work" / "ai_log.jsonl"
BLOCK_DIR = ROOT / "work" / "blocked"
MAX_BLOCKS = 3
DAY = 24 * 3600
SHAPE_BAN = [  # 止められたことのある形。依頼文にこれが入っていたら送らない
    r"謎解き",
    r"前の手番の(メモ|考え|推理|下書き)",
    r"(考えた|推理の|思考の?)(過程|中身|内容)を(すべて|全部|くわしく)?(書|出力|説明)",
    r"分かったこと.{0,40}テーマの候補.{0,40}方針",
    r'"(think|thinking|nazo|reasoning|thoughts?)"\s*:',
]
_guard_lock = threading.Lock()
_last_send = [0.0]


class Blocked(Exception):
    """AIに送らなかった、または安全確認に止められた。同じ文は送り直さない。"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _guard() -> dict:
    try:
        return json.loads(GUARD_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"hashes": {}, "times": []}


def _log(rec: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _kind(prompt: str) -> str:
    m = re.match(r"\s*【(.{1,8}?)】", prompt)
    return m.group(1) if m else "?"


def check_before_send(prompt: str, model: str) -> str:
    """送ってよければ文のハッシュを返す。だめなら Blocked（AIには何も送らない）。"""
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    rec = {"t": _now(), "model": model, "kind": _kind(prompt), "chars": len(prompt), "hash": h}
    for pat in SHAPE_BAN:
        if re.search(pat, prompt, re.S):
            _log({**rec, "result": "refused_shape"})
            raise Blocked("止められたことのある書き方が入っているので、AIに送りませんでした")
    with _guard_lock:
        g = _guard()
        if h in g["hashes"]:
            _log({**rec, "result": "refused_same"})
            raise Blocked("前に止められた文と同じなので、AIに送りませんでした")
        recent = [t for t in g["times"] if time.time() - t < DAY]
        if len(recent) >= MAX_BLOCKS:
            _log({**rec, "result": "refused_cap"})
            left = int((DAY - (time.time() - min(recent))) / 3600) + 1
            raise Blocked(f"24時間に{MAX_BLOCKS}回止められたので、AIへの送信を止めています（あと約{left}時間）")
    return h


def note_block(prompt: str, model: str, h: str, msg: str) -> None:
    with _guard_lock:
        g = _guard()
        g["hashes"][h] = {"t": _now(), "model": model, "kind": _kind(prompt)}
        g["times"] = [t for t in g["times"] if time.time() - t < DAY] + [time.time()]
        try:
            GUARD_FILE.parent.mkdir(exist_ok=True)
            GUARD_FILE.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
            BLOCK_DIR.mkdir(exist_ok=True)
            (BLOCK_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{h}.txt").write_text(f"# {model} / {msg[:200]}\n\n{prompt}", encoding="utf-8")
        except OSError:
            pass


def clean_env() -> dict:
    """Claude Code の中から起動しても、子の claude が自前のログインで動くように環境変数を外す。"""
    env = dict(os.environ)
    for k in list(env):
        if k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_") or k.startswith("CLAUDE_AGENT_SDK") or k == "ANTHROPIC_BASE_URL":
            env.pop(k)
    return env


def claude_cmd() -> list[str]:
    exe = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return [str(exe)] if exe.exists() else [shutil.which("claude") or "claude"]


# ---- 見えないデスクトップで動かす（Windows）
# claude は呼ぶたびに git・reg・PowerShell などを別の窓で起動し、画面に窓が点滅して手前に来る。
# 専用の見えないデスクトップの中で起動すれば、子の窓もすべてそちらに出て、ふだんの画面には何も出ない。
if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes as W

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _u32.CreateDesktopW.restype = W.HANDLE
    _u32.CreateDesktopW.argtypes = [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p]

    class _SA(ctypes.Structure):
        _fields_ = [("nLength", W.DWORD), ("lpSecurityDescriptor", W.LPVOID), ("bInheritHandle", W.BOOL)]

    class _SI(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                    ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                    ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                    ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD),
                    ("lpReserved2", W.LPVOID), ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]

    class _PI(ctypes.Structure):
        _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]

    _DESK_NAME = "ateruyo_ai_hidden"
    _desk = None
    _desk_lock = threading.Lock()

    def _desktop() -> str:
        global _desk
        with _desk_lock:
            if not _desk:
                _desk = _u32.CreateDesktopW(_DESK_NAME, None, None, 0, 0x10000000, None)  # GENERIC_ALL
                if not _desk:
                    raise OSError(ctypes.get_last_error(), "CreateDesktop 失敗")
        return _DESK_NAME

    def _pipe():
        r, w = W.HANDLE(), W.HANDLE()
        sa = _SA(ctypes.sizeof(_SA), None, True)
        if not _k32.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
            raise OSError(ctypes.get_last_error(), "CreatePipe 失敗")
        return r.value, w.value

    def run_hidden(cmd: list[str], data: str, env: dict, cwd: str, timeout: int) -> tuple[int, str, str]:
        in_r, in_w = _pipe(); out_r, out_w = _pipe(); err_r, err_w = _pipe()
        for h in (in_w, out_r, err_r):  # 親側の端は子に渡さない
            _k32.SetHandleInformation(W.HANDLE(h), 1, 0)
        si = _SI(); si.cb = ctypes.sizeof(_SI)
        si.lpDesktop = "WinSta0\\" + _desktop()
        si.dwFlags = 0x100 | 0x1  # STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        si.hStdInput, si.hStdOutput, si.hStdError = in_r, out_w, err_w
        pi = _PI()
        block = ctypes.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0")
        flags = 0x4000 | 0x400 | 0x08000000  # BELOW_NORMAL | UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
        ok = _k32.CreateProcessW(None, ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd)), None, None, True,
                                 flags, block, cwd, ctypes.byref(si), ctypes.byref(pi))
        for h in (in_r, out_w, err_w):
            _k32.CloseHandle(W.HANDLE(h))
        if not ok:
            for h in (in_w, out_r, err_r):
                _k32.CloseHandle(W.HANDLE(h))
            raise OSError(ctypes.get_last_error(), "CreateProcess 失敗")
        bufs = {}

        def reader(h, key):
            with open(msvcrt.open_osfhandle(h, 0), "rb") as f:
                bufs[key] = f.read()

        ts = [threading.Thread(target=reader, args=(out_r, "out"), daemon=True),
              threading.Thread(target=reader, args=(err_r, "err"), daemon=True)]
        for t in ts:
            t.start()
        with open(msvcrt.open_osfhandle(in_w, 0), "wb") as f:
            try:
                f.write(data.encode("utf-8"))
            except OSError:
                pass
        if _k32.WaitForSingleObject(W.HANDLE(pi.hProcess), timeout * 1000) != 0:
            _k32.TerminateProcess(W.HANDLE(pi.hProcess), 1)
        code = W.DWORD()
        _k32.GetExitCodeProcess(W.HANDLE(pi.hProcess), ctypes.byref(code))
        _k32.CloseHandle(W.HANDLE(pi.hProcess)); _k32.CloseHandle(W.HANDLE(pi.hThread))
        for t in ts:
            t.join(10)
        dec = lambda b: (b or b"").decode("utf-8", errors="replace")
        return code.value, dec(bufs.get("out")), dec(bufs.get("err"))


def run_claude(cmd: list[str], prompt: str) -> tuple[int, str, str]:
    """claude を1回動かす（テストではこれを差し替えて、本物を呼ばない）。"""
    if os.name == "nt":
        return run_hidden(cmd, prompt, clean_env(), str(WORK), 600)
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=clean_env(), timeout=600, cwd=str(WORK))
    return r.returncode, r.stdout, r.stderr


def ask(prompt: str, model: str) -> str:
    WORK.mkdir(exist_ok=True)
    check_before_send(prompt, model)  # 枠を待つ前に一度（すぐ断れるものは待たせない）
    cmd = claude_cmd() + ["-p", "--model", model, "--tools", "", "--no-session-persistence",
                          "--setting-sources", "", "--system-prompt", SYSTEM]
    with LIMIT:
        with _guard_lock:  # 送信の間隔をあける
            wait = _last_send[0] + MIN_GAP - time.time()
            _last_send[0] = max(time.time(), _last_send[0] + MIN_GAP)
        if wait > 0:
            time.sleep(wait)
        # 待っている間に、先に動いた分が止められて上限に届いたかもしれない。起動の直前にもう一度確かめる
        h = check_before_send(prompt, model)
        t0 = time.time()
        rc, out, err = run_claude(cmd, prompt)
        out = (out or "").strip()
        rec = {"t": _now(), "model": model, "kind": _kind(prompt), "chars": len(prompt), "sec": round(time.time() - t0, 1), "hash": h}
        # 止められた記録は、枠を空ける前に書く（待っている次の分が、古い数のまま送られないように）
        if re.search(r"safeguards? flagged|flagged this message", (out + " " + (err or ""))[:2000]) and (rc != 0 or len(out) < 400):
            note_block(prompt, model, h, out or err)
            _log({**rec, "result": "blocked"})
            raise Blocked("AIの安全確認に止められました。同じ文は送り直しません")
    if rc != 0 or not out:
        _log({**rec, "result": "error", "msg": (out or err).strip()[:200]})
        raise RuntimeError(f"claude 失敗 (code {rc}): {(out or err).strip()[:300]}")
    _log({**rec, "result": "ok"})
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/ateruyo", "/ateruyo.html"):
            f = ROOT / "ateruyo.html"
            build = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
            return self._send(200, f.read_bytes().replace(b"__BUILD__", build.encode()), "text/html; charset=utf-8")
        if self.path == "/api/ping":
            return self._json({"ok": True, "game": GAME})
        if self.path == "/api/guard":  # 送った回数・止められた回数（記録から数える）
            cnt = {}
            try:
                for x in LOG_FILE.read_text(encoding="utf-8").splitlines():
                    if x.strip():
                        k = json.loads(x).get("result", "?")
                        cnt[k] = cnt.get(k, 0) + 1
            except Exception:  # noqa: BLE001
                pass
            g = _guard()
            return self._json({"log": cnt, "blocked_24h": len([t for t in g["times"] if time.time() - t < DAY]), "max": MAX_BLOCKS})
        self._send(404, b"not found", "text/plain")

    def _from_game(self) -> bool:
        """このゲームの画面からの呼び出しだけを受け付ける。
        ほかのサイトを開いたブラウザが 127.0.0.1 に送ってきても、Claude を動かさない（Host・Origin・Content-Type を見る）。"""
        hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
        if self.headers.get("Host", "") not in hosts:  # DNS の付け替えで別の名前から来たもの
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in hosts}:
            return False
        return (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json"

    def do_POST(self):
        if self.path != "/api/ai":
            return self._send(404, b"not found", "text/plain")
        if not self._from_game():
            return self._send(403, b"forbidden", "text/plain")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            model = req.get("model") or TIER_MODEL.get(req.get("modelTier") or "default", TIER_MODEL["default"])
            if model not in MODEL_TIER:
                return self._json({"error": f"使えないモデル: {model}"}, 400)
            text = ask(str(req.get("prompt", "")), model)
            self._json({"text": text, "modelApplied": model, "modelTierApplied": MODEL_TIER[model]})
        except Blocked as e:
            self._json({"error": str(e), "blocked": True}, 200)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)


def main():
    url = f"http://127.0.0.1:{PORT}/"
    with socket.socket() as s:
        busy = s.connect_ex(("127.0.0.1", PORT)) == 0
    if busy:  # もう動いている。同じゲームなら画面だけ開き、もう一方のゲームなら知らせる
        try:
            import urllib.request
            other = json.loads(urllib.request.urlopen(url + "api/ping", timeout=3).read()).get("game", "")
        except Exception:  # noqa: BLE001
            other = ""
        if other == GAME:
            if "--no-browser" not in sys.argv:
                webbrowser.open(url)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"もう一方のゲーム（{other or '別のもの'}）が動いています。\nタスクバーにあるその黒い窓を閉じてから、開き直してください。", GAME, 0x40)
        return
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"{GAME}: {url}\nこの窓を閉じると終了します。")
    if "--no-browser" not in sys.argv:  # 測定（tools/bench.mjs）のときは画面を開かない
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
