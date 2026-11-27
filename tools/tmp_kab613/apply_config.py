import json
import sys

new = json.load(open(sys.argv[1]))
for path, field in (("config/xbrl/row_place_fixes.json", "fixes"), ("config/xbrl/row_merge_exclusions.json", "pairs")):
    lines = open(path, encoding="utf-8").read().rstrip().split("\n")
    end = max(i for i, line in enumerate(lines) if line.strip().startswith("]"))
    last = max(i for i in range(end) if lines[i].strip().startswith("{"))
    if not lines[last].rstrip().endswith(","):
        lines[last] = lines[last].rstrip() + ","
    add = [("    " + json.dumps(x, ensure_ascii=False)) for x in new[field]]
    add = [a + "," for a in add[:-1]] + add[-1:]
    lines[end:end] = add
    text = "\n".join(lines) + "\n"
    json.loads(text)
    open(path, "w", encoding="utf-8").write(text)
