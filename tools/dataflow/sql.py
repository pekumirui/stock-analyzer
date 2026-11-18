"""SQLite に流れた SQL 1 文から、書くテーブルと読むテーブルを取り出す。

完全な構文解析はしない。拾うのは INSERT / REPLACE / UPDATE / DELETE の対象と、FROM / JOIN の後ろの名前。
文字列リテラル・コメント・CTE の名前は除く。DDL（CREATE / ALTER / DROP）と PRAGMA は数えない。
"""
from __future__ import annotations

import re
from functools import lru_cache

_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)'
_QUALIFIED = rf"(?:{_IDENT}\.)?({_IDENT})"

_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_SKIP_RE = re.compile(r"^\s*(?:CREATE|ALTER|DROP|PRAGMA|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|VACUUM|ANALYZE|REINDEX|ATTACH|DETACH|EXPLAIN)\b", re.I)
_CTE_RE = re.compile(rf"(?:\bWITH(?:\s+RECURSIVE)?|,)\s*({_IDENT})\s*(?:\([^)]*\)\s*)?AS\s*(?:NOT\s+)?(?:MATERIALIZED\s*)?\(", re.I)
_WRITE_RE = re.compile(
    rf"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+(?!SET\b){_QUALIFIED}",
    re.I,
)
_FROM_RE = re.compile(r"\b(FROM|JOIN)\s+", re.I)
# FROM の後ろのカンマ区切りの並びを、ここまでで切る
_FROM_END_RE = re.compile(
    r"\b(?:WHERE|GROUP|ORDER|LIMIT|HAVING|UNION|EXCEPT|INTERSECT|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|NATURAL|ON|USING|SET|RETURNING|WINDOW|VALUES|SELECT)\b|[();]",
    re.I,
)
_FIRST_NAME_RE = re.compile(rf"\s*{_QUALIFIED}")


def _unquote(name: str) -> str:
    if name[:1] in '"`[':
        return name[1:-1]
    return name


def _strip(sql: str) -> str:
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _STRING_RE.sub("''", sql)
    return _LINE_COMMENT_RE.sub(" ", sql)


@lru_cache(maxsize=4096)
def _tables_cached(sql: str) -> tuple[frozenset[str], frozenset[str]]:
    if _SKIP_RE.match(sql):
        return frozenset(), frozenset()
    ctes = {_unquote(m.group(1)).lower() for m in _CTE_RE.finditer(sql)}

    writes: set[str] = set()
    for m in _WRITE_RE.finditer(sql):
        writes.add(_unquote(m.group(1)).lower())
    # DELETE FROM x の x を読みに数えないよう、書く句を消してから FROM を探す
    rest = _WRITE_RE.sub(" ", sql)

    reads: set[str] = set()
    for m in _FROM_RE.finditer(rest):
        tail = rest[m.end():]
        if m.group(1).upper() == "JOIN":
            first = _FIRST_NAME_RE.match(tail)
            if first:
                reads.add(_unquote(first.group(1)).lower())
            continue
        end = _FROM_END_RE.search(tail)
        segment = tail[: end.start()] if end else tail
        if not segment.strip() and tail.lstrip().startswith("("):
            continue
        for part in segment.split(","):
            first = _FIRST_NAME_RE.match(part)
            if first:
                reads.add(_unquote(first.group(1)).lower())
    return frozenset(writes - ctes), frozenset(reads - ctes)


def tables(sql: str) -> tuple[frozenset[str], frozenset[str]]:
    """(書くテーブル, 読むテーブル)。名前は小文字。同じ表を読んで書く文は両方に入る。"""
    return _tables_cached(_strip(sql))
