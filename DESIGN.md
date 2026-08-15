# 日本株分析ツール 設計書

## 1. 全体像

```
┌─────────────┐   日次バッチ(19:30 JST)   ┌──────────────┐
│ J-Quants API │ ─────────────────────→ │  DB           │
│ (Light plan) │   上場銘柄/株価/財務/TOPIX │  SQLite(ローカル)│
└─────────────┘                          │  Postgres(本番) │
┌─────────────┐   補完(必要時)            └──────┬───────┘
│ IRBank MCP  │ ─────────────────────→        │
└─────────────┘                                ↓
                                    ┌──────────────────┐
                                    │ FastAPI (REST API) │
                                    │ + 静的SPA配信       │
                                    └──────┬───────────┘
                                           │ HTTPS
                                 ┌─────────┴─────────┐
                                 │  Web / スマホブラウザ │
                                 └───────────────────┘
```

- **言語**: Python 3.10+ / FastAPI / SQLAlchemy / pandas
- **フロント**: 単一HTML SPA (lightweight-charts + vanilla JS)。レスポンシブでスマホ対応
- **バッチ**: `scripts/run_batch.py` を cron / スケジューラで日次実行

## 2. J-Quants API V2 / Light プランの前提

> **V1は閉鎖予定のためV2を使用する。** V1とは認証方式・エンドポイント・レスポンス構造・
> カラム名がすべて変わっている。

### 認証
APIキー方式。ダッシュボードで発行したキーを `x-api-key` ヘッダーに付与するだけ。
有効期限がないためトークンリフレッシュ処理は不要（V1のidToken/refreshTokenは廃止）。

### 使用エンドポイント

| 用途 | V2エンドポイント | 備考 |
|---|---|---|
| 上場銘柄一覧 | `/v2/equities/master` | 17/33業種・市場区分・貸借信用区分 |
| 株価四本値 | `/v2/equities/bars/daily` | **調整済OHLCV(AdjO/H/L/C/Vo)と時価総額を直接取得可** |
| TOPIX四本値 | `/v2/indices/bars/daily/topix` | from/to指定 |
| 財務情報 | `/v2/fins/summary` | 決算短信サマリ |
| 取引カレンダー | `/v2/markets/calendar` | 営業日判定 (HolDiv=="1") |

レスポンスは全て `{"data": [...], "pagination_key": "..."}` 形式。
カラム名は短縮形（`Open`→`O`, `Close`→`C`, `AdjustmentClose`→`AdjC` 等）。

### データ範囲 (Light)
過去5年。RS計算に必要な252営業日+αには十分。

### レートリミット ★設計上の最重要制約

| プラン | 上限 |
|---|---|
| Free | 5 req/分 |
| **Light** | **60 req/分** |
| Standard | 120 req/分 |
| Premium | 500 req/分 |

加えて `/v2/fins/summary` と `/v2/fins/details` はプランと別枠で60 req/分。
超過すると429、大幅超過だと5分間の全遮断。

→ `jquants.py` でエンドポイント群ごとにスライディングウィンドウのトークンバケットを持ち、
自主規制する。429を受けたら指数バックオフ (5→10→20→40→60秒) で最大6回再試行。

### 取得方式 ★V1設計からの変更点

公式ベストプラクティスに「**1銘柄ずつ×全日付での取得を避けてください**」と明記されている。
`date` のみ指定すれば全上場銘柄のその日のデータが1リクエストで取れる。

| 方式 | リクエスト数(5年) | Lightでの所要時間 |
|---|---|---|
| 銘柄ループ (旧設計) | 約4,000銘柄 × 2 = 8,000 | 約2時間 |
| **日付ループ (採用)** | 約1,220営業日 × 2 = 2,440 | **約45分** |

さらに高速な選択肢として、Lightプラン以上ではダッシュボードから**CSV一括ダウンロード**が
利用できる。ただしCSVには調整済株価が含まれず自前計算が必要なため、
本ツールでは調整済株価をAPIから直接取れる日付ループ方式を採用する。
（10年以上の長期バックテストをやりたくなったらCSV + 自前調整を検討）

## 3. DBスキーマ

```sql
stocks            -- 銘柄マスタ
  code TEXT PK    -- 5桁コード (例 "72030")
  name, sector33, market, updated_at

daily_prices      -- 日足
  code, date PK(複合)
  open, high, low, close, volume, turnover      -- 調整前(報告時生値)
  adj_factor
  adj_open, adj_high, adj_low, adj_close, adj_volume  -- 調整済(分析はこちらを使う)
  market_cap                                     -- 時価総額(百万円)

topix             -- TOPIX日足
  date PK, open, high, low, close

statements        -- 決算短信 (J-Quants /fins/statements ほぼ生保存)
  disclosure_no PK
  code, disclosed_date, disclosed_time
  type_of_period          -- 1Q/2Q/3Q/FY
  fiscal_year_start, fiscal_year_end
  net_sales, operating_profit, ordinary_profit, profit, eps   -- 累計実績
  f2_net_sales, f2_operating_profit, f2_ordinary_profit, f2_profit, f2_eps  -- 半期(2Q)会社予想
  fy_net_sales, fy_operating_profit, fy_ordinary_profit, fy_profit, fy_eps  -- 通期会社予想
  is_revision             -- 予想修正開示フラグ
  raw_json                -- 元データ全項目 (将来の項目追加に備え保持)

indicators        -- 日次算出テクニカル (バッチで更新, 最新日分を保持+履歴)
  code, date PK(複合)
  ma50, ma150, ma200
  ma200_slope_1m          -- 200日線の1ヶ月変化率
  high_52w, low_52w
  rs_raw, rs_rank         -- RS生値と1-99ランク
  stage                   -- 1/2/3/4 (判定不能はNULL)
  avg_volume_50

earnings_events   -- 決算発表イベントと発表後パフォーマンス
  code, disclosed_date PK(複合)
  base_price              -- 発表当日(場中発表)or翌営業日始値
  return_1d, return_5d, return_20d, return_60d, return_to_date
  alpha_1d, ... alpha_to_date   -- 対TOPIX超過リターン
```

### Q単独値の算出 (保存せずAPI側で導出)
`Q単独 = 当期累計 − 前四半期累計`(1Qはそのまま)。前年同期のQ単独と比較しYoY成長率を計算。EPSも同様。

`CurPerType` は `1Q/2Q/3Q/4Q/5Q/FY` を取る。FYと4Qは通期累計として同一視。
5Q(決算期変更等の変則期)は前後関係が定義できないためQ単独を算出しない。

### 予想修正の検知
同一事業年度の直前開示の通期営業利益予想と比較し、±0.1%以上の差があれば
上方/下方修正として修正率(%)付きでバッジ表示する。

### 会計基準による欠損 ★注意
IFRS・米国基準の開示には**経常利益の概念が無く `OdP` は空欄**になる。
経常利益でのスクリーニングやソートは欠損前提で扱うこと(営業利益を主指標にする)。

## 4. 分析ロジック

### 4.1 レラティブストレングス (RS)
```
rs_raw = ((C-C63)/C63*0.4 + (C-C126)/C126*0.2 + (C-C189)/C189*0.2 + (C-C252)/C252*0.2) * 100
```
adj_close を使用。全銘柄(算出可能な上場銘柄)で降順に並べ、上位1%→99、次の1%→98…と1-99を割当。

### 4.2 ミネルヴィニ・ステージ判定
優先順位付きルールベース (Stage2テンプレート → Stage4 → Stage1 → Stage3 → その他)。

**Stage 2 (全条件AND)**: C > MA150 かつ C > MA200 / MA150 > MA200 / MA200が1ヶ月以上上昇 (`ma200_slope_1m > 0`) / MA50 > MA150 > MA200 / C > MA50 / C ≥ 52週安値×1.3 / C ≥ 52週高値×0.75 / RSランク ≥ 70

**Stage 4**: C < MA200 かつ MA200下降 (`ma200_slope_1m < -閾値`) かつ Cが52週安値の1.1倍以内

**Stage 1**: C < 52週安値×1.3 圏で MA200±8%以内に株価が滞留 (直近60日の帯域内比率で判定) かつ MA200がほぼ横ばい (|slope| < 閾値)

**Stage 3**: 直近まで Stage2 だったが MA200 が横ばい化 (+ボラ拡大: 直近20日ATR/株価が上昇)

閾値は `config.py` に集約し調整可能。

### 4.3 決算日からの相対パフォーマンス
発表日基準価格から各営業日の `(株価リターン − TOPIXリターン)` を算出。チャート画面にマーカー表示し、テーブルで +1d/+5d/+20d/+60d/現在 を表示。

## 5. API設計 (抜粋)

| Method/Path | 内容 |
|---|---|
| `GET /api/earnings?date=2026-08-14` | その日の決算サマリ一覧 (累計+Q単独+YoY+予想+修正フラグ) |
| `GET /api/earnings/calendar?month=2026-08` | 日別の決算件数 (カレンダー表示用) |
| `GET /api/chart/{code}?tf=D|M&ma=50,150,200&rel=topix` | 株価チャートデータ |
| `GET /api/earnings-performance/{code}` | 決算日からの対TOPIXリターン |
| `GET /api/screener?stage=2&rs_min=70&sort=rs` | ステージフィルタ |
| `GET /api/stocks/search?q=トヨタ` | 銘柄検索 |

## 6. 日次バッチ (19:30 JST)

1. `/equities/master` で銘柄マスタ更新
2. `/equities/bars/daily?date=当日` + `/indices/bars/daily/topix` 取込
3. `/fins/summary?date=当日` 取込
4. indicators 再計算 (MA/52週/RS/ステージ) → earnings_events 更新

日次は **計4リクエスト** で完了する。決算開示は15時以降が多いため、
当日夜(19:30)と翌朝6:00の2回実行して取りこぼしを防ぐ。

冪等設計 (UPSERT) のため何度実行しても安全。バックフィルは取得済みの日付を
スキップするので、中断しても再実行すれば続きから埋まる。

## 7. ホスティング (低コストPaaS案)

| 役割 | サービス | 費用 |
|---|---|---|
| API+SPA | **Fly.io** (shared-cpu 256MB) or Railway | $0〜5/月 |
| DB | **Neon** or Supabase (Postgres 無料枠 0.5GB) | ¥0 |
| バッチ | Fly.io Machines のスケジュール起動 or GitHub Actions cron | ¥0 |
| 認証 | Basic認証 or Cloudflare Access (個人利用) | ¥0 |

データ量目安: 株価4,000銘柄×5年×250日 ≈ 500万行 ≈ 300-400MB → 無料枠内はギリギリ。本番は直近2年+月足集約で圧縮するか、Neon有料枠($19)へ。まずは全部SQLiteのままFly.io永続ボリューム(3GB無料)に置く構成が最安・最簡単。

ローカル→本番は `DATABASE_URL` 切替のみ (SQLAlchemyで両対応)。

## 8. 将来拡張

- **米国株**: データソース差替え(Polygon/Alpaca等)を想定し、`source` 列と市場別カレンダー・通貨を吸収するアダプタ層を`jquants.py`と同一インターフェースで追加
- **IRBank MCP**: J-Quantsに無い項目 (セグメント、株主、信用残日次) の補完
- 通知 (Stage2入り銘柄・上方修正をLINE/メール)

## 9. ローカル実行手順

```powershell
.\setup.ps1              # .venv作成 + 依存インストール
.\run.ps1 seed           # デモデータ投入 (J-Quants不要)
.\run.ps1 serve          # → http://localhost:8000

# 実データへ切替
.\run.ps1 reset          # デモDB削除 (混在するとRSランクが狂うため必須)
Copy-Item .env.example .env ; notepad .env    # APIキーを記入
.\run.ps1 check          # 疎通 + 項目マッピング確認
.\run.ps1 backfill       # 5年分 (約45分)
```
