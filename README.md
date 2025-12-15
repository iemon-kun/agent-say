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
- `speak(text, engine="auto", speed=1.0, timeout_seconds=20.0, warmup=False, wait_for_completion=False, dedupe_seconds=30.0, hard_timeout_seconds=600.0)`
  - `engine`: `auto` / `say` / `swift` / `espeak`（`auto` は利用可能なものを優先）
  - `speed`: 話速倍率（`1.0` が標準、例: `1.2`）。`say`/`espeak` は WPM 相当に変換し、`swift` は `AVSpeechUtterance.rate` を倍率で調整します（体感が完全一致する保証はありません）
  - `timeout_seconds`: 読み上げ 1 回あたりのタイムアウト（`wait_for_completion=True` のときにのみ使用。デフォルト時は自動計算し、推定が 300 秒未満でも 300 秒に張り付き、推定が 300 秒以上なら推定値を採用します。明示指定がある場合はその値を下限として扱います）
  - `warmup`: `True` で先に短い発話（「ウォームアップ」）を行い、初回遅延を減らす
  - `wait_for_completion`: `False`（デフォルト）だと非同期で開始だけ行い、すぐに成功応答を返します（呼び出し側のツール呼び出しタイムアウトで再試行される問題を避けやすくなります）
  - 同時実行上限: 2（上限を超えると `Speech busy...` を返して開始しません）
  - `dedupe_seconds`: 同一テキストの重複実行をこの秒数だけ抑止します（短時間の再試行で二重読み上げになるのを防ぎます）
  - `hard_timeout_seconds`: 非同期実行時のハード上限（秒）。異常に長引く/ハングするケースの保険です
  - 読み上げ速度をエンジン別に観測して移動平均を更新し、次回以降の自動タイムアウト推定に使います。
  - 返り値には `mode`（`async`/`sync`）や `hard_timeout` など、実際に適用された状態を括弧付きで含めます。

- `stop_speech(all=True)`
  - 実行中の読み上げを停止します（`all=False` で直近 1 件のみ停止）

## 備考
- 標準出力は MCP の JSON-RPC 用、ログは標準エラーに出力します。
- `TMPDIR` が未設定または書き込み不可なら `agent-say/tmp` を自動作成し、環境変数を上書きして利用します。
