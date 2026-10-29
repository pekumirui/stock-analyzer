"""表は共通の table.sheet を使う（KAB-273。static/AGENTS.md）。

テンプレート・JS・CSS を読み、(a) sheet クラスの無い <table>（JS 生成も含む）、
(b) 共通 CSS 以外で表（table / th / td 等）に font-size を指定している箇所を落とす。
共通 CSS はグローバルな table/th/td と table.sheet（およびその子孫）だけ。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREEN = ROOT / "static"
AGENTS_LINE = "表は共通のtable.sheetを使い、表ごとにフォントサイズや書式を指定しない。"

TABLE_TAG_RE = re.compile(r"<table\b([^>]*)>", re.I)
CREATE_TABLE_RE = re.compile(r"""createElement\(\s*(['"])table\1\s*\)""", re.I)
CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*(?:(['"])(.*?)\1|(\S+))""", re.I)
ID_ATTR_RE = re.compile(r"""\bid\s*=\s*(?:(['"])(.*?)\1|(\S+))""", re.I)
STYLE_ATTR_RE = re.compile(r"""\bstyle\s*=\s*(['"])(.*?)\1""", re.I)
TABLE_CELL_TAG_RE = re.compile(
    r"<(table|thead|tbody|tfoot|tr|th|td|caption|colgroup|col)\b([^>]*)>",
    re.I,
)
CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
FONT_SIZE_DECL_RE = re.compile(r"(?<![\w-])font-size\s*:", re.I)
TABLE_TYPE_RE = re.compile(
    r"(?<![\w-])(table|thead|tbody|tfoot|tr|th|td|caption|colgroup|col)(?![\w-])",
    re.I,
)
SHEET_IN_SEL_RE = re.compile(
    r"(?:^|[\s>+~,(])(?:table)?\.sheet\b",
    re.I,
)


def _screen_files() -> list[Path]:
    return sorted(
        p
        for ext in ("html", "js", "css")
        for p in SCREEN.rglob(f"*.{ext}")
        if p.is_file()
    )


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def _attr_value(match: re.Match[str]) -> str:
    return match.group(2) if match.group(1) else match.group(3)


def _class_tokens(attrs: str) -> list[str]:
    m = CLASS_ATTR_RE.search(attrs)
    if not m:
        return []
    return _attr_value(m).split()


def _has_sheet_class(attrs: str) -> bool:
    return "sheet" in _class_tokens(attrs)


def _id_token(attrs: str) -> str | None:
    m = ID_ATTR_RE.search(attrs)
    if not m:
        return None
    raw = _attr_value(m).strip()
    return raw or None


def _hit(path: Path, text: str, start: int, snippet: str | None = None) -> str:
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    line_no = text[:start].count("\n") + 1
    if snippet is None:
        snippet = text.splitlines()[line_no - 1].strip()
    return f"{_rel(path)}:{line_no}: {snippet}"


def _var_gets_sheet(var: str, window: str) -> bool:
    return bool(
        re.search(
            rf"""\b{re.escape(var)}\.classList\.add\(\s*(['"])sheet\1\s*\)"""
            rf"""|\b{re.escape(var)}\.className\s*=\s*(['"]).*?\bsheet\b""",
            window,
        )
    )


def _tables_without_sheet(path: Path, text: str) -> list[str]:
    out = []
    for m in TABLE_TAG_RE.finditer(text):
        if not _has_sheet_class(m.group(1)):
            out.append(_hit(path, text, m.start()))
    lines = text.splitlines()
    for m in CREATE_TABLE_RE.finditer(text):
        chained = text[m.end() : m.end() + 80]
        if re.match(
            r"""\s*\.class(?:List\.add\(\s*(['"])sheet\1|Name\s*=\s*(['"]).*?\bsheet\b)""",
            chained,
        ):
            continue
        prefix = text[max(0, m.start() - 60) : m.start()]
        assigned = re.search(
            r"(?:(?:const|let|var)\s+)?(\w+)\s*=\s*(?:document\.)?$",
            prefix,
        )
        if assigned:
            lineno = text[: m.start()].count("\n")
            window = "\n".join(lines[lineno : lineno + 8])
            if _var_gets_sheet(assigned.group(1), window):
                continue
        out.append(_hit(path, text, m.start()))
    return out


def _table_classes_and_ids(files: list[Path]) -> tuple[set[str], set[str]]:
    classes: set[str] = set()
    ids: set[str] = set()
    for path in files:
        if path.suffix.lower() == ".css":
            continue
        text = path.read_text(encoding="utf-8")
        for m in TABLE_TAG_RE.finditer(text):
            attrs = m.group(1)
            classes.update(t for t in _class_tokens(attrs) if not t.startswith("$"))
            tid = _id_token(attrs)
            if tid and not tid.startswith("$"):
                ids.add(tid)
    return classes, ids


def _blank_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def _is_common_table_selector(sel: str) -> bool:
    s = re.sub(r"::?[\w-]+(?:\([^)]*\))?", "", sel)
    s = " ".join(s.split())
    if SHEET_IN_SEL_RE.search(s):
        return True
    if "#" in s or "." in s:
        return False
    return True


def _selector_targets_table(sel: str, table_classes: set[str], table_ids: set[str]) -> bool:
    if TABLE_TYPE_RE.search(sel):
        return True
    for name in table_classes:
        if name == "sheet":
            continue
        if re.search(rf"(?<![\w-])\.{re.escape(name)}(?![\w-])", sel):
            return True
    for name in table_ids:
        if re.search(rf"#{re.escape(name)}(?![\w-])", sel):
            return True
    return False


def _css_font_size_hits(
    path: Path, text: str, table_classes: set[str], table_ids: set[str]
) -> list[str]:
    out = []
    blanked = _blank_css_comments(text)
    for m in CSS_RULE_RE.finditer(blanked):
        raw_sel = m.group(1).strip()
        body = m.group(2)
        if raw_sel.startswith("@") or not FONT_SIZE_DECL_RE.search(body):
            continue
        bad = []
        for sel in raw_sel.split(","):
            sel = sel.strip()
            if not sel or not _selector_targets_table(sel, table_classes, table_ids):
                continue
            if _is_common_table_selector(sel):
                continue
            bad.append(" ".join(sel.split()))
        if bad:
            out.append(_hit(path, text, m.start(), ", ".join(bad)))
    return out


def _inline_table_font_size(path: Path, text: str) -> list[str]:
    out = []
    for m in TABLE_CELL_TAG_RE.finditer(text):
        style = STYLE_ATTR_RE.search(m.group(2))
        if style and FONT_SIZE_DECL_RE.search(style.group(2)):
            out.append(_hit(path, text, m.start()))
    return out


def scan_table_sheet(files: list[Path]) -> tuple[list[str], list[str]]:
    no_sheet: list[str] = []
    font_size: list[str] = []
    table_classes, table_ids = _table_classes_and_ids(files)
    for path in files:
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix in {".html", ".js"}:
            no_sheet.extend(_tables_without_sheet(path, text))
            font_size.extend(_inline_table_font_size(path, text))
        if suffix == ".css":
            font_size.extend(_css_font_size_hits(path, text, table_classes, table_ids))
    return no_sheet, font_size


def test_static_agents_md_states_sheet_rule() -> None:
    text = (SCREEN / "AGENTS.md").read_text(encoding="utf-8").strip()
    assert text == AGENTS_LINE


def test_detector_catches_table_without_sheet(tmp_path: Path) -> None:
    html = tmp_path / "a.html"
    html_lines = [
        '<table class="sheet"></table>',
        '<table class="fin"></table>',
        "<table></table>",
        '<table id="earn-tbl" class="sheet"></table>',
        '<div class="tbl-wrap"></div>',
    ]
    html.write_text("\n".join(html_lines), encoding="utf-8")
    js = tmp_path / "a.js"
    js_lines = [
        'h += `<table class="sheet"><thead>`;',
        'wrap.innerHTML = `<table class="monthly-tl"></table>`;',
        "pop.innerHTML = `<table>${head}</table>`;",
        'const t = document.createElement("table");',
        "box.appendChild(t);",
        'const ok = document.createElement("table");',
        'ok.classList.add("sheet");',
    ]
    js.write_text("\n".join(js_lines), encoding="utf-8")
    no_sheet, _ = scan_table_sheet([html, js])
    got = {(h.split(":")[0], h.split(":")[1]) for h in no_sheet}
    assert got == {("a.html", "2"), ("a.html", "3"), ("a.js", "2"), ("a.js", "3"), ("a.js", "4")}


def test_detector_catches_per_table_font_size(tmp_path: Path) -> None:
    html = tmp_path / "a.html"
    html_lines = [
        '<table class="supply-tbl"></table>',
        '<td style="font-size:11px">x</td>',
        '<span style="font-size:12px">単位</span>',
    ]
    html.write_text("\n".join(html_lines), encoding="utf-8")
    css = tmp_path / "a.css"
    css_lines = [
        "table { font-size:13px; }",
        "table.sheet th { font-size:11px; }",
        "table.sheet td.num { font-size:13px; }",
        "table.sheet .g { font-size:11px; }",
        "th,td { padding:4px; font-size:12px; }",
        "table.fin th { font-size:11px; }",
        ".supply-tbl { width:auto; font-size:12px; }",
        ".monthly-pop table { font-size:13px; }",
        "h3 { font-size:14px; }",
        ".cal .d .n { font-size:12px; }",
    ]
    css.write_text("\n".join(css_lines), encoding="utf-8")
    js = tmp_path / "a.js"
    js.write_text('svg += `<text font-size="10"></text>`;\n', encoding="utf-8")
    _, font_size = scan_table_sheet([html, js, css])
    got = {(h.split(":")[0], h.split(":")[1]) for h in font_size}
    assert got == {("a.html", "2"), ("a.css", "6"), ("a.css", "7"), ("a.css", "8")}


def test_tables_use_sheet_class() -> None:
    no_sheet, _ = scan_table_sheet(_screen_files())
    assert not no_sheet, "sheet クラスの無い <table>（static/AGENTS.md）:\n" + "\n".join(no_sheet)


def test_no_per_table_font_size() -> None:
    _, font_size = scan_table_sheet(_screen_files())
    assert not font_size, (
        "共通 CSS 以外で表に font-size（static/AGENTS.md）:\n" + "\n".join(font_size)
    )
