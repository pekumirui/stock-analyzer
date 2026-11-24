# モジュールの依存

入口は [OVERVIEW.md](../OVERVIEW.md)。

`app`・`jobs`・`mcp`・`tools`・`analyzer` の Python の `import` を AST で読み、パッケージ単位にまとめた。関数の中の `import` も数える。

- パッケージは `app/<機能>`・`jobs/<機能>`・`tools/<名前>`・`mcp`・`analyzer`。各フォルダの直下のファイルは `app`・`jobs`・`tools` の名前で 1 つにする
- `vendor` は `app/xbrlp` と `app/edinet_wrapper` をまとめた 1 つの箱
- ほぼ全部が使う共通（`app` 直下の db・models・config など、`jobs` 直下）へ向かう線は図から省く。表には出す
- 青い枠はその図の主役、灰色の点線の枠はそれ以外。点線の矢印は `app` が `jobs` を使っている線（「jobs と app」の図）
- `import` があるだけで、実行時に呼ぶかは見ない。テストは数えない

## app の中

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["app"]:::own
  n1["app/api"]:::own
  n2["app/edinet"]:::own
  n3["app/market"]:::own
  n4["app/monthly"]:::own
  n5["app/supply"]:::own
  n6["app/xbrl"]:::own
  n7["vendor"]:::own
  n0 --> n1
  n0 --> n3
  n0 --> n4
  n0 --> n6
  n1 --> n3
  n1 --> n4
  n1 --> n5
  n1 --> n6
  n2 --> n6
  n2 --> n7
  n3 --> n6
  n6 --> n2
  n6 --> n7
  classDef own fill:#e3ecff,stroke:#3b5bdb
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

## jobs と app

矢印は `import` する側から、される側へ。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["app/edinet"]:::ext
  n1["app/market"]:::ext
  n2["app/monthly"]:::ext
  n3["app/supply"]:::ext
  n4["app/xbrl"]:::ext
  n5["jobs"]:::own
  n6["jobs/edinet"]:::own
  n7["jobs/market"]:::own
  n8["jobs/monthly"]:::own
  n9["jobs/ops"]:::own
  n10["jobs/supply"]:::own
  n11["jobs/xbrl"]:::own
  n12["vendor"]:::ext
  n5 --> n1
  n5 --> n2
  n6 --> n0
  n6 --> n4
  n6 --> n12
  n7 --> n1
  n7 --> n8
  n7 --> n9
  n8 --> n2
  n10 --> n3
  n11 --> n0
  n11 --> n4
  n11 --> n12
  classDef own fill:#e3ecff,stroke:#3b5bdb
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

## tools・mcp・analyzer

これらを向く線・これらから出る線。`app`・`jobs` 直下（共通）への線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["analyzer"]:::own
  n1["app/api"]:::ext
  n2["app/market"]:::ext
  n3["app/monthly"]:::ext
  n4["app/supply"]:::ext
  n5["app/xbrl"]:::ext
  n6["jobs/market"]:::ext
  n7["jobs/monthly"]:::ext
  n8["jobs/ops"]:::ext
  n9["mcp"]:::own
  n10["tools/briefing"]:::own
  n11["tools/dataflow"]:::own
  n12["tools/edinet"]:::own
  n13["tools/formal"]:::own
  n14["tools/jobs"]:::own
  n15["tools/market"]:::own
  n16["tools/monthly"]:::own
  n17["tools/overview"]:::own
  n18["tools/schema"]:::own
  n19["tools/screen"]:::own
  n20["tools/supply"]:::own
  n21["tools/tdnet_pdf"]:::own
  n22["tools/wiki"]:::own
  n23["tools/xbrl"]:::own
  n24["vendor"]:::ext
  n0 --> n2
  n7 --> n16
  n9 --> n5
  n10 --> n2
  n10 --> n6
  n10 --> n7
  n10 --> n8
  n11 --> n14
  n11 --> n18
  n11 --> n19
  n12 --> n24
  n13 --> n2
  n14 --> n11
  n15 --> n2
  n15 --> n5
  n16 --> n1
  n16 --> n3
  n16 --> n7
  n17 --> n11
  n17 --> n14
  n18 --> n11
  n19 --> n11
  n20 --> n4
  n21 --> n5
  n22 --> n18
  n23 --> n5
  n23 --> n24
  classDef own fill:#e3ecff,stroke:#3b5bdb
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

## パッケージごとの依存先

図で省いた共通も含む。

| パッケージ | 依存先 |
|---|---|
| `analyzer` | `app`, `app/market` |
| `app` | `app/api`, `app/market`, `app/monthly`, `app/xbrl` |
| `app/api` | `app`, `app/market`, `app/monthly`, `app/supply`, `app/xbrl` |
| `app/edinet` | `app`, `app/xbrl`, `vendor` |
| `app/market` | `app`, `app/xbrl` |
| `app/monthly` | `app` |
| `app/supply` | `app` |
| `app/xbrl` | `app`, `app/edinet`, `vendor` |
| `jobs` | `app`, `app/market`, `app/monthly` |
| `jobs/edinet` | `app`, `app/edinet`, `app/xbrl`, `vendor` |
| `jobs/market` | `app`, `app/market`, `jobs`, `jobs/monthly`, `jobs/ops` |
| `jobs/monthly` | `app`, `app/monthly`, `jobs`, `tools/monthly` |
| `jobs/ops` | `app`, `jobs` |
| `jobs/supply` | `app`, `app/supply`, `jobs` |
| `jobs/xbrl` | `app`, `app/edinet`, `app/xbrl`, `jobs`, `vendor` |
| `mcp` | `app`, `app/xbrl` |
| `tools` |  |
| `tools/briefing` | `app`, `app/market`, `jobs`, `jobs/market`, `jobs/monthly`, `jobs/ops` |
| `tools/dataflow` | `app`, `tools/jobs`, `tools/schema`, `tools/screen` |
| `tools/edinet` | `app`, `vendor` |
| `tools/formal` | `app`, `app/market` |
| `tools/golden` | `app` |
| `tools/jobs` | `tools/dataflow` |
| `tools/lint` |  |
| `tools/market` | `app`, `app/market`, `app/xbrl` |
| `tools/monthly` | `app`, `app/api`, `app/monthly`, `jobs`, `jobs/monthly` |
| `tools/ops` |  |
| `tools/overview` | `tools/dataflow`, `tools/jobs` |
| `tools/schema` | `app`, `tools/dataflow` |
| `tools/screen` | `app`, `tools/dataflow` |
| `tools/supply` | `app/supply` |
| `tools/tdnet_pdf` | `app`, `app/xbrl` |
| `tools/wiki` | `tools/schema` |
| `tools/xbrl` | `app`, `app/xbrl`, `vendor` |
| `vendor` |  |
