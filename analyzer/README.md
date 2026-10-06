# analyzer

`stock.db` を読むだけのオフライン分析。アプリ本体（`app/`）・バッチ・UI からは使わない。

## 約束

- DB は読み取り専用で開く（`file:...?mode=ro`）。書き込まない
- 1 つの問いに 1 フォルダ。`<テーマ>/` に実行スクリプトと結果の `README.md` を置く
- 結果の `README.md` には、問い・データ期間・定義・結論・実行コマンドを書く。数字は実行時点のもの
- 生成物（CSV・パネル）は `--out` で `/tmp` などリポジトリ外へ出す。コミットするのは結果の要約とグラフの PNG だけ
- 分析期間と確認期間を分ける。分析期間だけで良く見えた結論は採らない
- 2 つ目の分析で同じ処理（価格の調整、月末判定、Newey-West など）が要ったら `common/` に切り出す。それまでは各スクリプトに置く
- 例外: `agent_forecast/` は stock.db を読まない。開示（IR Bank）と本番の月次 API を材料にして `claude -p` を回す。予想に渡した財務 JSON も `results/` に残す
- 依存は `analyzer/requirements.txt`（`.venv/bin/python -m pip install -r analyzer/requirements.txt`）

## 一覧

| フォルダ | 問い | 結論 | 日付 |
|---|---|---|---|
| [`momentum/`](momentum/README.md) | 過去 3 か月（と 12-1）の対 TOPIX 超過リターンは、次の 3 か月を予測するか | 予測力は確認できない | 2026-10-02 |
| [`agent_forecast/`](agent_forecast/README.md) | 決算予想をエージェントにさせるとき、月次（PDF / 月次 API）と説明資料は精度とトークンにどう効くか | 売上は月次で決まり、利益は月次＋説明資料で当たる。月次は API の方が少ないトークンで同じ精度（ＫｅｅＰｅｒ技研 1 件、各条件 1 回） | 2026-10-07 |
