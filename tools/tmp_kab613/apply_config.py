import json
import sys
from pathlib import Path

new = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for path, field in (("config/xbrl/row_place_fixes.json", "fixes"), ("config/xbrl/row_merge_exclusions.json", "pairs")):
    lines = Path(path).read_text(encoding="utf-8").rstrip().split("\n")
    end = max(i for i, line in enumerate(lines) if line.strip().startswith("]"))
    last = max(i for i in range(end) if lines[i].strip().startswith("{"))
    if not lines[last].rstrip().endswith(","):
        lines[last] = lines[last].rstrip() + ","
    if not new[field]:
        continue
    add = [("    " + json.dumps(x, ensure_ascii=False)) for x in new[field]]
    add = [a + "," for a in add[:-1]] + add[-1:]
    lines[end:end] = add
    text = "\n".join(lines) + "\n"
    json.loads(text)
    Path(path).write_text(text, encoding="utf-8")
