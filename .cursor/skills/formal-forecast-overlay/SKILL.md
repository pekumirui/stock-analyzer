---
name: formal-forecast-overlay
description: "業績予想・配当予想を開示ごとに項目単位で上書きする共通の層（app/market/forecasts.py の forecast_points）に、短い開示の並びを全通り流して性質を確かめる。forecast_points・withdrawals.py・analytics.py の is_actual / is_quarter_flash_revision、それを使う revisions.py・chart_fund.py・api/stock.py の配当予想を変えたとき、または「予想 PER がずれる」「配当予想が古い」を調べるときに使う。"
---

# formal-forecast-overlay

`forecast_points` は 1 銘柄の開示を古い順に畳み、項目ごとに「その時点で分かっていた最新の予想」を作る。これに、開示の種類・空欄・取り下げの組み合わせを長さ 3 まで全通り作って本物の関数に流し、下の性質を確かめる。模型は書かない（写し間違いが入らない）。

## 走らせる

```sh
python -m tools.formal.forecast_overlay.check --length 2 \
  --expect OlderReportChangesNothing --expect OnlyTheCurrentYearIsOverlaid
python -m tools.formal.forecast_overlay.check --length 3 \
  --kinds fy_report_prior,q1_report,q2_report,revision,quarter_flash
```

- KAB-551 が main に入るまでは `app/market/forecasts.py` が main に無い。`improve/KAB-551` を別の場所に `git worktree add --detach` し、そこで `PYTHONPATH=<その場所> python <この repo>/tools/formal/forecast_overlay/check.py ...` と流す
- 期待どおりなら exit 0。期待にない性質が破れるか、期待した違反が出なければ exit 1 で、破れた性質ごとに最短の並びを 1 つ出す
- 長さ 2（全 7 種類、28,392 通り）は数秒。長さ 3 は種類を絞って 1,728,000 通りで数分
- `--expect` は下の既知の不具合。直ったら外す

## 開示の種類（`KINDS`）

| 名前 | 何か | 対象の年度 |
|---|---|---|
| `fy_report_prior` | 前期の本決算短信（来期予想 `NxF*` を持つ） | 今期 |
| `q3_report_prior` | 前期の 3Q 決算短信。前期の本決算より後に来れば古い期の短信（出し直し） | 前期 |
| `q1_report` / `q2_report` | 今期の 1Q・2Q 決算短信（`fy_*`） | 今期 |
| `revision` | 今期の業績予想の修正（`EarnForecastRevision`、`FY`） | 今期 |
| `revision_prior` | 前期の業績予想の修正 | 前期 |
| `quarter_flash` | 1Q・3Q 型の予想修正（四半期の速報値、KAB-553） | 今期 |

それぞれ `profit`・`eps` を出すか空か、年間配当予想を出すか空か、取り下げの印（なし・`profit`・`div_annual`）を全通り組む。

## 性質

| 名前 | 約束 |
|---|---|
| `InputOrderDoesNotMatter` | 渡す順を入れ替えても結果は同じ |
| `QuarterFlashLeavesForecastAlone` | 四半期の速報値は通期予想を変えない |
| `OlderReportChangesNothing` | 古い期の決算短信が後から来ても、どの項目も変えない（配当予想も） |
| `OnlyTheCurrentYearIsOverlaid` | 終わった期についての開示は、今の予想を変えない |
| `BlankInRevisionCarriesOver` | 予想修正で空の項目は前の値のまま |
| `WithdrawnBecomesUndecided` | 取り下げた項目は未定（空）になる |
| `ValueComesFromItsSource` | 値の出どころの開示が、本当にその値（または EPS の元の純利益）を出している |

## 既知の不具合（2026-11-24、KAB-559 で発見。`improve/KAB-551` の `9d640e4`）

1. **古い期の決算短信が配当予想を書き換える**（`OlderReportChangesNothing`）: `forecast_points` は古い期の短信を `IGNORED` にするが、年間配当予想だけはその短信の値で上書きする。
   - 本番で古い期の短信が新しい期の短信より後に来た例は 570 件・276 社。うち 456 件が配当予想を持つ
   - 93680 は 2026-10-29 に過去 2 期の短信を出し直した。本物の関数に流すと、配当予想の最新値が 2025 年 3 月期（分割前）の 60 円になる。正しくは 38 円
2. **終わった期の予想修正が今期の予想を書き換える**（`OnlyTheCurrentYearIsOverlaid`）: 本決算と同じ日、本決算より後に終わった期の予想修正が来ると、来期予想（今期の予想）がその値で上書きされる。
   - 本番で 228 件・162 社。うち 103 件が業績予想の値を持つ
   - 57410 は 2026-05-14 の本決算で予想 EPS 154.64、同じ日の前期の予想修正で 214.75 になり、次の 1Q 短信（2026-08-06）まで続く。株価チャートの予想 PER がその間ずれる。修正履歴（`revisions.py`）は年度ごとに分けるので影響しない

## 本物の不具合か、検査の誤りか

- 出た並びを `SimpleNamespace` の開示で組み、`forecast_points` に流して値を見る。期待した値でなければ本物
- 本番に同じ並びがあるかを `statements` で数える（上の件数は本番を読み取りで数えたもの）。無い並びなら、生成する種類を減らす前にユーザーに相談する
- 性質の判定（`violations`）を緩めて通さない

## 範囲

1 銘柄・2 年度・項目は `profit` と `eps`（ほかの業績項目は空）と年間配当。開示の日はすべて別の日。株数の変化と分割（`fc_eps_scale`・`adjust_per_share`）、`revisions.py` の年度ごとの組み立て、`config/market/forecast_withdrawals.json` の一覧は入れていない（取り下げの印は `withdrawn_forecasts` で渡す）。

## 同期

- 対象: `app/market/forecasts.py`、`app/market/withdrawals.py`、`app/market/analytics.py`（`is_actual`・`is_quarter_flash_revision`・`ORDER`）
- 最後に確かめたコミット: `improve/KAB-551` の `9d640e4`（main に未マージ）
- `git log 9d640e4..HEAD -- app/market/forecasts.py app/market/withdrawals.py app/market/analytics.py` に何か出たら、`KINDS` と `violations` がまだコードの意味に合うかを見直してから同期コミットを進める
