"""保存済みの結果 JSON を、LLM を呼ばずに照合・検算し直す。extract.py の照合・検算を直したあとに使う。

    .venv/bin/python analyzer/deck_reader/recheck.py /workspace/deck-reader/results/*_haiku_v2_elow.json

- 各ページの text（テキスト層）と facts から verified・検算をやり直し、stats を更新して同じファイルに書く
- facts の中身（LLM が読んだ値）は変えない。値の末尾の単位の重なりだけは外す（extract.strip_unit）
- OCR の照合は、結果 JSON に残したページの ocr_numbers を使う（PDF も OCR も要らない）
"""
import argparse
import json
import pathlib

import extract as d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", type=pathlib.Path, help="run.py の結果 JSON")
    a = ap.parse_args()
    for path in a.results:
        res = json.loads(path.read_text())
        before = res.get("stats", {})
        res["pages"] = [d.recheck(p) for p in res["pages"]]
        res["stats"] = dict(before, **d.stats(res["pages"]))
        path.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        s = res["stats"]
        print(f"{path.name}: verified {before.get('verified_ratio')} -> {s['verified_ratio']}  "
              f"検算不一致 {before.get('sum_mismatch')} -> {s['sum_mismatch']} / {s['sum_checks']}（一部だけ {s['sum_partial']}）")


if __name__ == "__main__":
    main()
