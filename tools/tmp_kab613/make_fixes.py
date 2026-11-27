import json
import sys

fixes, pairs, seen = [], [], set()
for line in open(sys.argv[1]):
    d = json.loads(line)
    if d["kind"] == "child":
        for key in d["keys"]:
            if (d["code"], d["consolidated"], d["doc_id"], key) in seen:
                continue
            seen.add((d["code"], d["consolidated"], d["doc_id"], key))
            fixes.append({
                "code": d["code"], "consolidated": d["consolidated"], "statement": "CF", "doc_id": d["doc_id"], "key": key,
                "evidence": f"KAB-613。{d['period_end']} の{'有報' if d['source'] == 'edinet' else '短信 XBRL'}は {key} を "
                            f"{d['parent']} の子に宣言。値は親の内訳でない（親 {d['parent_value']:,.0f}、子の和 {d['sum']:,.0f}）",
            })
        continue
    pre, post = d["pre"], d["post"]
    side, docs = ("post", post) if len(post) <= len(pre) else ("pre", pre)
    mark = "PostSubtotal" if side == "post" else "PreSubtotal"
    target = f"{d['key']}#{mark}"
    other = pre if side == "post" else post
    where = "小計の後ろ" if side == "post" else "小計の前"
    there = "小計の前" if side == "post" else "小計の後ろ"
    for doc, source, end, value in docs:
        if (d["code"], d["consolidated"], doc, d["key"]) in seen:
            continue
        seen.add((d["code"], d["consolidated"], doc, d["key"]))
        fixes.append({
            "code": d["code"], "consolidated": d["consolidated"], "statement": "CF", "doc_id": doc,
            "key": d["key"], "as": target,
            "evidence": f"KAB-613。{end} の{'有報' if source == 'edinet' else '短信 XBRL'}は {d['key']} を営業の{where}に宣言（値 {value:,.0f}）。"
                        f"ほかの {len(other)} 通（{other[0][2]}〜{other[-1][2]}）は{there}。同じ要素で別の行なので分ける",
        })
    pairs.append({"code": d["code"], "statement": "CF", "keys": [d["key"], target],
                  "evidence": f"KAB-613。{d['key']} の{where}の行（{len(docs)} 通）と{there}の行。別の行"})
json.dump({"fixes": fixes, "pairs": pairs}, sys.stdout, ensure_ascii=False, indent=1)
