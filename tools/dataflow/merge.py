from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.dataflow.merge")
    parser.add_argument("mode", choices=("check", "write"))
    parser.add_argument("dumps", nargs="+", type=Path)
    args = parser.parse_args(argv)
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(tempfile.mkdtemp()) / 'stock.db'}")
    from tools.dataflow import plugin

    for path in args.dumps:
        plugin._merge_seen(json.loads(path.read_text(encoding="utf-8")))

    def line(text: str, **_colors: bool) -> None:
        print(text, flush=True)

    return 0 if plugin.finish(args.mode, line) else 1


if __name__ == "__main__":
    raise SystemExit(main())
