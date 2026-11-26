"""claude.ai に出す版（ateruyo_artifact.html）を、ateruyo.html から作る。AIには何も送らない。
python tools/build_artifact.py
- 立ち絵（STAND）を外す。チャットのClaudeが書き写せる大きさにするため（絵がないと、ページは両者を表の上の段に並べる）
- 写しちがいの点検の値（SUM）を入れる。書き写したページの台本が1字でも変わっていたら、左下に「写しちがい」と出る
ateruyo.html を直したら、これを実行して作り直す。ateruyo_artifact.html は手で直さない。
"""
import io
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent
SUM_RE = r'const SUM = "[^"]*"'


def imul(a, b):
    return (a * b) & 0xFFFFFFFF


def hash_of(s):
    """ページの hashOf と同じ計算（文字は UTF-16 の単位で読む）"""
    raw = s.encode("utf-16-le")
    a, b = 0xDEADBEEF, 0x41C6CE57
    for i in range(0, len(raw), 2):
        c = raw[i] | raw[i + 1] << 8
        a = imul(a ^ c, 2654435761)
        b = imul(b ^ c, 1597334677)
    a = imul(a ^ (a >> 16), 2246822507) ^ imul(b ^ (b >> 13), 3266489909)
    b = imul(b ^ (b >> 16), 2246822507) ^ imul(a ^ (a >> 13), 3266489909)
    n = 4294967296 * (2097151 & b) + a
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + out
    return out or "0"


def script_sum(html):
    body = html[html.index("<script>") + len("<script>"):html.rindex("</script>")]
    body = re.sub(SUM_RE, 'const SUM = ""', body, count=1)
    return hash_of(re.sub(r"[ \t\r\n]+", "", body))


def build():
    src = (root / "ateruyo.html").read_text(encoding="utf-8")
    out, n = re.subn(r"^const STAND = \{.*\};$", "const STAND = {};", src, count=1, flags=re.M)
    if n != 1:
        sys.exit("立ち絵の行（const STAND = {…};）が見つからない")
    if "data:image" in out:
        sys.exit("絵のデータが残っている")
    if len(re.findall(SUM_RE, out)) < 1:
        sys.exit("const SUM の行が見つからない")
    out = re.sub(SUM_RE, f'const SUM = "{script_sum(out)}"', out, count=1)
    dst = root / "ateruyo_artifact.html"
    dst.write_text(out, encoding="utf-8", newline="\n")
    return dst, out


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    dst, out = build()
    print(f"{dst.name}: {len(out.encode('utf-8')) // 1024}KB・{out.count(chr(10)) + 1}行")
