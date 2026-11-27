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
- 自分の claude.ai に置きたいときは、`ateruyo_artifact.html` をチャットに添付して「そのまま Artifact として表示して」と頼んでください。
- Anthropic の公式のものではありません。立ち絵は画像生成 AI で作りました。

[MIT](LICENSE)
