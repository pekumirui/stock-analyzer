import json
import sqlite3
import sys
from pathlib import Path


def main():
    db_path, labels_path = sys.argv[1], sys.argv[2]
    apply = "--apply" in sys.argv[3:]
    fixes = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    uri = f"file:{db_path}?mode={'rw' if apply else 'ro'}"
    con = sqlite3.connect(uri, uri=True)
    hits = []
    for ns, local, wrong, right in fixes:
        for (eid,) in con.execute(
            "SELECT id FROM xbrl_elements WHERE namespace_uri = ? AND local_name = ? AND label_ja = ?", (ns, local, wrong),
        ):
            hits.append((eid, ns, local, wrong, right))
    for eid, ns, local, wrong, right in hits:
        print(eid, ns.rsplit("/", 2)[-2], local, wrong[:30], "->", right, sep="\t")
    print(f"rows={len(hits)} apply={apply}")
    if apply:
        with con:
            con.executemany("UPDATE xbrl_elements SET label_ja = ? WHERE id = ? AND label_ja = ?",
                            [(right, eid, wrong) for eid, _ns, _local, wrong, right in hits])
        print("updated", con.total_changes)


if __name__ == "__main__":
    main()
