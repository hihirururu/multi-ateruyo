# 複数人アテルーヨー

あなたが「この〇人、だ～れだ？」のお題を出し、Claude の Opus と Sonnet が質問と解答で早当てを競うのを見るゲームです。審判も Opus です。

![対局の画面](screenshot.png)

**遊ぶ:** https://claude.ai/artifact/4Z2f7uuA5g6AGQ7fqJGy6R （Claude にログインして開いてください）

1局で AI を約60回呼ぶので、あなたの Claude の使用量を多く使います。

## 自分の PC で遊ぶ（立ち絵つき）
`claude` コマンド（[Claude Code](https://claude.com/claude-code)）と Python 3.10 以上が要ります。

```
python server.py
```

Windows は `アテルーヨー.bat` でも起動できます。

## メモ
- AI の安全確認に止められた依頼は、二度と送りません。
- `ateruyo_artifact.html` は `python tools/build_artifact.py` で作る claude.ai 用の版です。
- Anthropic の公式のものではありません。立ち絵は画像生成 AI で作りました。

[MIT](LICENSE)
