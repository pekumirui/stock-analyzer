---
name: formal-forecast-overlay
description: "業績予想・配当予想を開示ごとに項目単位で上書きする共通の層（app/market/forecasts.py の forecast_points）の規則を TLA+ で全順序検査する。forecast_points・report_keys・is_older_report・_report_values・_revision_values、withdrawals.py の withdrawn_items、analytics.py の is_actual・is_quarter_flash_revision・ORDER を変えたとき、または「予想 PER がずれる」「配当予想が古い」「訂正の後に予想が戻る」を調べるときに使う。"
---

# formal-forecast-overlay

1 銘柄の開示が何本、どの順で、どんな空欄で来ても、項目ごとの「その時点の予想」が 1 つに決まり、会社が言った意味どおりになるかを、TLC で全部の並びについて確かめる。

- 約束: 最後に残る予想は、いま会社が出している年度について、最後にその項目に触れた開示の値（取り下げなら未定）。同じ時刻に出た開示の並べ方で結果が変わらない
- 破れたときの害: 株価チャートの予想 PER、銘柄の概要の予想 PER と年間配当予想が、古い値・別の年度の値・未定になる。次の決算短信まで続く

## 走らせる

```sh
tools/formal/check.sh forecast_overlay MCPerf.cfg
tools/formal/check.sh forecast_overlay MCDiv.cfg
tools/formal/check.sh forecast_overlay FindingOlderReportDividend.cfg
tools/formal/check.sh forecast_overlay FindingStaleRevision.cfg
tools/formal/check.sh forecast_overlay FindingBlankCorrection.cfg
tools/formal/check.sh forecast_overlay FindingCorrectionAfterRevision.cfg
tools/formal/check.sh forecast_overlay FindingUnmarkedWithdrawal.cfg
```

- Java と `tla2tools.jar` は [formal-tdnet-pdf-retire](../formal-tdnet-pdf-retire/SKILL.md) と同じ
- `MC*.cfg` は開示 4 本までで 1 本 15〜30 分ほど。`Finding*.cfg` は 3 本までで数秒。模型は `tools/formal/forecast_overlay/ForecastOverlay.tla`

今のコードでの正しい結果:

| cfg | 期待 | 意味 |
|---|---|---|
| `MCPerf.cfg` | `No error has been found`（1,208,659 distinct states） | 業績予想（1 項目で代表）の回帰検査。古い期の決算短信・空欄の訂正・予想修正の後の訂正・終わった期の予想修正も入れる |
| `MCDiv.cfg` | `No error has been found`（2,891,641 distinct states） | 年間配当予想の回帰検査。古い期の決算短信・空欄の訂正・予想修正の後の訂正・終わった期の予想修正も入れる |
| `FindingOlderReportDividend.cfg` | `No error has been found` | 直した不具合（KAB-565） |
| `FindingStaleRevision.cfg` | `No error has been found` | 直した不具合（KAB-566） |
| `FindingBlankCorrection.cfg` | `No error has been found` | 直した不具合（KAB-555） |
| `FindingCorrectionAfterRevision.cfg` | `No error has been found` | 直した不具合（KAB-567） |
| `FindingUnmarkedWithdrawal.cfg` | `Matches` 違反 | 既知の限界 1 |

Finding の cfg が通るようになったら、その不具合が直ったということ。直した変更の PR で、その cfg の期待を「違反なし」に書き換え、下の既知の不具合から外す。回帰検査に入れたいなら、`MC*.cfg` の対応する `Allow*` を `TRUE` にする。

反例を読むときは pstack の `/statechart` スキルを使う。

## 模型とコードの対応

| 模型 | コード |
|---|---|
| `seq` | 1 銘柄の `statements`。並びは本当に出た順 |
| `kind = "report"` | `is_actual` が真（`type_of_document` に `FinancialStatements`） |
| `form = "original"` / `"restate"` / `"blank"` | 最初の決算短信 / 同じ期の 2 本目（訂正）で予想の欄に元の短信の値を載せ直したもの / 予想の欄が空のもの。コードでは同じ `(fiscal_year_end, type_of_period, type_of_document)` の 2 本目（区分 `correction`）で、前の版から変わった項目だけを上書きする。模型の訂正は値を変えないので、`CodeStep` では触れていない |
| `q = 4` | 本決算。予想は来期（`NxF*`・`NxFDivAnn`）なので `Target` は翌年度 |
| `kind = "revision"` | `is_actual` が偽で `is_quarter_flash_revision` が偽 |
| `kind = "flash"` | `is_quarter_flash_revision` が真（1Q・3Q 型の `EarnForecastRevision`）。業績の欄は四半期の累計値 `"vq"` |
| `marked` | `withdrawn_items` がその項目を返す |
| `slot` | `(disclosed_date, disclosed_time)`。同じ `slot` の中の並びはコードでは `disclosure_no` で決まり、本当の順は無い |
| `Item = "perf"` / `"div"` | `FORECAST_ITEMS` のどれか 1 つ / `DIVIDEND_ITEM` |
| `CodeFold` | `forecast_points` の 1 項目分。`latest` は `report_keys` の最新、`Older` は `is_older_report`、`held` は最後に使った決算短信の予想の年度（`r.year < held` が `is_stale`） |
| `SpecState` | あるべき意味。いま会社が出している年度（`target`）について、触れた開示の値を順に当てる。古い期の決算短信、訂正短信、速報値の業績の欄は「触れていない」 |

`Records` は 1 本の開示の取りうる形。`CanAppend` は現実にありうる並びだけに絞る前提で、`Allow*` は不具合を起こす並びを入れるかどうか。

| 定数 | 真にすると入る並び |
|---|---|
| `AllowOlderReport` | 既に見た決算短信より古い期の決算短信（過年度の訂正の再提出、遅れて出た短信） |
| `AllowBlankCorrection` | 予想の欄が空の訂正短信 |
| `AllowCorrectionAfterRevision` | 元の短信と訂正短信の間（または訂正と同じ時刻）に、同じ年度の予想修正がある |
| `AllowStaleRevision` | いまの年度より前の年度の予想修正（本決算と同じ時刻に出たものを含む） |
| `AllowUnmarkedWithdrawal` | 取り下げの印の無い取り下げ |

ほかの前提: 年度は 2 つ、四半期は 2Q と本決算だけ。値は 2 種類。最初の開示は決算短信。同じ期の最初の決算短信は 1 本。同じ時刻の決算短信は 1 本で、同じ時刻に同じ年度の同じ項目に触れる開示どうしは同じ値を言う。決算期変更（年度の始まりと年度末で新旧が食い違う）、`fiscal_year_start` の誤り、EPS を純利益と株数から作る計算、株式分割、`revisions.py` の年度ごとの組み立てと一覧のバッジは入れていない。

## 性質

- `Matches`: 本当に出た順に畳んだコードの値が、あるべき値と同じ
- `OrderIndependent`: 同じ時刻の開示をどう並べ替えても、コードの値が変わらない

## 既知の不具合（2026-11-24、KAB-559 で発見。どれも本物の `forecast_points` に反例の並びを流して再現した）

古い期の決算短信が配当予想を書き換える不具合（KAB-565）、終わった期の予想修正が来期の予想を書き換える不具合（KAB-566）、訂正短信の空欄が未定になる不具合（KAB-555）、予想修正の後の訂正短信が修正前の予想に戻す不具合（KAB-567）は直した。どれも `MC*.cfg` の `Allow*` で回帰検査に入っている。

1. **印の無い取り下げは前の値のまま**（`FindingUnmarkedWithdrawal.cfg`）: J-Quants の空欄は「触れていない」と「取り下げ」を見分けられないので、印（短信 XBRL か `config/market/forecast_withdrawals.json`）が無い取り下げは拾えない。仕様どおり（`docs/jobs/market.md`「予想の取り下げの印」）で、直すなら印を足す

## 本物の不具合か、模型の誤りか

- 反例の各開示を `SimpleNamespace` の `statements` 行（`type_of_document`・`type_of_period`・`fiscal_year_end`・`fy_*`・`raw_json` の `FDivAnn` など・`withdrawn_forecasts`）に置き換え、`forecast_points` に流す。`latest_forecast` が `SpecState` の値と違えば本物
- 同じ並びが本番にあるかを `statements` で数える
- 違わなければ模型の誤り。たいていは `CodeStep` の写し漏れか、`SpecIntent` の意味の取り違え。模型を直す。性質を弱めて通さない

## コードを変えたら

1. 変更が `description` に挙げた関数に触れるかを見る。触れなければ何もしない
2. 触れるなら、`CodeStep` を変更に合わせて直す。新しい種類の開示・新しい欄の読み方・新しい年度の比べ方は、それぞれ `Records` か `CodeStep` の分岐にする
3. 7 本の cfg を流し、上の表の期待と比べる。`MC*.cfg` が落ちたら変更が約束を壊したので、反例をコードの言葉で報告する
4. 模型がコードを正しく写していると確かめたら、下の同期コミットを進める

## 同期

- 対象: `app/market/forecasts.py`、`app/market/withdrawals.py`、`app/market/analytics.py`（`is_actual`・`is_quarter_flash_revision`・`ORDER`）
- 最後に模型とコードの一致を確かめたコミット: `0d3b915`
- `git log 0d3b915..HEAD -- app/market/forecasts.py app/market/withdrawals.py app/market/analytics.py` に何か出たら、模型を見直してから同期コミットを進める
