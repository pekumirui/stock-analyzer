---
name: irbank-segment-revenue-external
description: IR Bank list_segments の「売上」は外部顧客への売上高。セグメント利益は有報に無い値のことがある
metadata:
  type: reference
---

IR Bank MCP `list_segments` の revenue は有報セグメント注記の「外部顧客への売上高」で、セグメント間を足した「計」ではない。突き合わせは `xbrl_segments` の `seg_external_sales` とする。`fiscal_year=2025` が 2025/03 期。

operating_income は会社の物差しと違うことがある（伊藤忠 8001 は有報に無い値。有報の物差しは当社株主帰属当期純利益）。ソフトバンク G は null。KAB-77 で確認（2026-10-20）。関連: [[verify-live-db]]
