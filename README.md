## 概要
`agent-say` はこのリポジトリで使う Model Context Protocol (MCP) サーバーで、エージェントの返答を音声で読み上げるツール `speak` を提供します。`say`（macOS）、Swift スクリプト、`espeak` の順で利用可能なエンジンを選び、Markdown を簡易的に除去して読み上げます。`TMPDIR` が未設定または書き込み不可なら、ワークスペース直下の `agent-say/tmp` を自動作成して `TMPDIR` を上書きします。

## 依存関係
- Python 3.13+
- 依存解決用のツール: `uv`（推奨、`uv.lock` あり）または `pip`
- 音声エンジンのいずれか: `say`（macOS 標準） / `swift` / `espeak`

## セットアップ
1. 音声エンジンを用意します（macOS なら `say` が標準で入っています。Linux なら `espeak` をインストールするなど）。
2. Swift を使う場合は `swift_tts.swift` に実行権限を付けます。
   ```bash
   chmod +x swift_tts.swift
   ```
3. 依存関係を入れます。
   - 推奨: `uv.lock` に従ってローカル `.venv` を作る
     ```bash
     uv sync --frozen
     source .venv/bin/activate
     ```
   - `pip` を使う場合（ロックは無視されます）
     ```bash
     python -m venv .venv
     source .venv/bin/activate
     pip install -e .
     ```

## 起動方法（MCP サーバー）
STDIO サーバーとして起動します。MCP 対応クライアントの設定例:
```json
{
  "mcpServers": {
    "agent-say": {
      "command": "uvx",
      "args": [
        "--from",
        "file:///ABSOLUTE/PATH/TO/agent-say",
        "python",
        "/ABSOLUTE/PATH/TO/agent-say/main.py"
      ]
    }
  }
}
```
`config.toml` 派生のクライアントの場合は例えば次のように書けます（パスは自分の環境に置換してください）:
```toml
[mcpServers.agent-say]
command = "uvx"
args = [
  "--from",
  "file:///ABSOLUTE/PATH/TO/agent-say",
  "python",
  "/ABSOLUTE/PATH/TO/agent-say/main.py",
]
```

ロックを使った使い捨て実行をしたい場合は `uvx --from file://$PWD python main.py` でも起動できます（カレントを `agent-say` にして実行）。

ローカルで直接試す場合は、仮想環境を有効化したうえで `cd agent-say && python main.py` または `uv run python main.py` を実行してください。

## ツール仕様
- `speak(text, engine="auto", timeout_seconds=20.0, warmup=False)`
  - `engine`: `auto` / `say` / `swift` / `espeak`（`auto` は利用可能なものを優先）
  - `timeout_seconds`: 読み上げ 1 回あたりのタイムアウト（デフォルト時は自動計算し、推定が 300 秒未満でも 300 秒に張り付き、推定が 300 秒以上なら推定値を採用します。明示指定がある場合はその値を優先します）
  - `warmup`: `True` で先に短い発話（「ウォームアップ」）を行い、初回遅延を減らす
  - 読み上げ速度をエンジン別に観測して移動平均を更新し、次回以降の自動タイムアウト推定に使います。

## 備考
- 標準出力は MCP の JSON-RPC 用、ログは標準エラーに出力します。
- `TMPDIR` が未設定または書き込み不可なら `agent-say/tmp` を自動作成し、環境変数を上書きして利用します。
