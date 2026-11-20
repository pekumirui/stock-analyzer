from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("no_comments_baseline.json")
REBUILD = "python -m tools.lint.no_comments --write"

VENDOR = ("app/xbrlp/", "app/edinet_wrapper/", "static/vendor/")
SKIPPED = frozenset({
    ".md", ".mdc", ".json", ".yml", ".yaml", ".toml", ".txt", ".csv", ".tsv", ".html", ".xml", ".xsd",
    ".gz", ".zip", ".png", ".svg", ".xlsx", ".xls", ".service", ".timer", ".conf", ".nft", ".lua",
    ".rhai", ".sql", ".ps1", ".example", ".gitkeep", ".gitignore", ".gitattributes", "",
})

_PY_DIRECTIVE = re.compile(
    r"#\s*(noqa(:\s*[A-Z]+[0-9]+(\s*,\s*[A-Z]+[0-9]+)*)?"
    r"|nosec(\s+B[0-9]+(\s*,\s*B[0-9]+)*)?"
    r"|type:\s*ignore(\[[\w\-, ]+\])?"
    r"|pragma:\s*no cover)\s*"
)
_SH_DIRECTIVE = re.compile(r"#\s*shellcheck\s+\S.*")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_JS_REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%<>~^")
_JS_REGEX_WORDS = frozenset({"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "yield", "await"})


def _python(src: str) -> dict[str, int]:
    comments = 0
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        if tok.start == (1, 0) and tok.string.startswith("#!"):
            continue
        if not _PY_DIRECTIVE.fullmatch(tok.string):
            comments += 1
    docstrings = sum(
        ast.get_docstring(node, clean=False) is not None
        for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return {"comment": comments, "docstring": docstrings}


def _js_template(src: str, i: int) -> tuple[int, bool]:
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
        elif c == "`":
            return i + 1, False
        elif src.startswith("${", i):
            return i + 2, True
        else:
            i += 1
    return i, False


def _javascript(src: str) -> dict[str, int]:
    comments = 0
    i, n = 0, len(src)
    prev, word = "", ""
    braces: list[bool] = []
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif src.startswith("//", i):
            comments += 1
            end = src.find("\n", i)
            i = n if end < 0 else end
        elif src.startswith("/*", i):
            comments += 1
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c in "\"'":
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
            i += 1
            prev, word = "s", ""
        elif c == "`" or (c == "}" and braces and braces[-1]):
            if c == "}":
                braces.pop()
            i, in_expr = _js_template(src, i + 1)
            if in_expr:
                braces.append(True)
            prev, word = "s", ""
        elif c == "{":
            braces.append(False)
            prev, word = c, ""
            i += 1
        elif c == "}":
            if braces:
                braces.pop()
            prev, word = c, ""
            i += 1
        elif c == "/" and (prev == "" or prev in _JS_REGEX_AFTER or word in _JS_REGEX_WORDS):
            i += 1
            in_class = False
            while i < n and src[i] != "\n" and (in_class or src[i] != "/"):
                if src[i] == "\\":
                    i += 1
                elif src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                i += 1
            i += 1
            prev, word = "s", ""
        elif c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            prev, word = "w", src[i:j]
            i = j
        else:
            prev, word = c, ""
            i += 1
    return {"comment": comments}


def _css(src: str) -> dict[str, int]:
    return {"comment": len(re.findall(r"/\*.*?\*/", src, re.S))}


def _sh_comment_start(line: str) -> int | None:
    quote = ""
    for i, c in enumerate(line):
        if quote:
            if c == "\\" and quote == '"':
                continue
            if c == quote and (i == 0 or line[i - 1] != "\\" or quote == "'"):
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return i
    return None


def _shell(src: str) -> dict[str, int]:
    comments = 0
    until = None
    for no, line in enumerate(src.split("\n"), 1):
        if until is not None:
            if line.strip() == until:
                until = None
            continue
        start = _sh_comment_start(line)
        code = line if start is None else line[:start]
        m = _HEREDOC.search(code)
        if m:
            until = m.group(2)
        if start is None:
            continue
        text = line[start:]
        if (no == 1 and text.startswith("#!")) or _SH_DIRECTIVE.fullmatch(text.rstrip()):
            continue
        comments += 1
    return {"comment": comments}


COUNTERS = {".py": _python, ".js": _javascript, ".css": _css, ".sh": _shell}


def count(path: str, src: str) -> dict[str, int]:
    return {k: v for k, v in COUNTERS[Path(path).suffix](src).items() if v}


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, text=True).stdout
    return sorted(p for p in out.split("\0") if p and not p.startswith(VENDOR))


def scan(root: Path = ROOT) -> tuple[dict[str, dict[str, int]], list[str]]:
    found: dict[str, dict[str, int]] = {}
    unknown: list[str] = []
    for rel in tracked(root):
        suffix = Path(rel).suffix
        if suffix in COUNTERS:
            path = root / rel
            if not path.is_file():
                continue
            n = count(rel, path.read_text(encoding="utf-8"))
            if n:
                found[rel] = n
        elif suffix not in SKIPPED:
            unknown.append(rel)
    return found, unknown


def diff(found: dict[str, dict[str, int]], baseline: dict[str, dict[str, int]]) -> tuple[list[str], list[str]]:
    more, fewer = [], []
    for rel in sorted(set(found) | set(baseline)):
        for kind in ("comment", "docstring"):
            now, was = found.get(rel, {}).get(kind, 0), baseline.get(rel, {}).get(kind, 0)
            if now > was:
                more.append(f"{rel}: {kind} {was} → {now}")
            elif now < was:
                fewer.append(f"{rel}: {kind} {was} → {now}")
    return more, fewer


def load_baseline(path: Path = BASELINE) -> dict[str, dict[str, int]]:
    return json.loads(path.read_text(encoding="utf-8"))


def problems(root: Path = ROOT, baseline_path: Path = BASELINE) -> list[str]:
    found, unknown = scan(root)
    more, fewer = diff(found, load_baseline(baseline_path))
    out = []
    if unknown:
        out.append("検査するかどうかが決まっていない拡張子。tools/lint/no_comments.py の COUNTERS か SKIPPED に足す:")
        out += [f"  {p}" for p in unknown]
    if more:
        out.append("コメント・docstring が増えた。足さない（制約はテストか名前で表す。経緯は commit / PR / issue）:")
        out += [f"  {m}" for m in more]
    if fewer:
        out.append(f"コメント・docstring が減った。台帳を直す（`{REBUILD}`）:")
        out += [f"  {m}" for m in fewer]
    return out


def write(root: Path = ROOT, baseline_path: Path = BASELINE) -> None:
    found, _ = scan(root)
    baseline_path.write_text(json.dumps(found, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if argv == ["--write"]:
        write()
        return 0
    out = problems()
    print("\n".join(out))
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
