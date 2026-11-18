"""KAB-147: xbrl_financials.unit は fact が pure のときだけ std.unit。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-fin-unit-')}/stock.db",
)

from app.xbrl.normalize import financial_unit, is_pure_unit


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_is_pure_unit() -> None:
    if not is_pure_unit("pure") or not is_pure_unit("PURE") or not is_pure_unit("xbrli:pure"):
        _fail("pure variants")
    if not is_pure_unit("") or not is_pure_unit(None) or not is_pure_unit("  "):
        _fail("empty is pure")
    if is_pure_unit("JPY") or is_pure_unit("USD") or is_pure_unit("USDPerShares") or is_pure_unit("shares"):
        _fail("monetary/shares are not pure")


def test_financial_unit_pure_uses_std() -> None:
    if financial_unit("persons", "pure") != "persons":
        _fail(financial_unit("persons", "pure"))
    if financial_unit("years", "xbrli:pure") != "years":
        _fail(financial_unit("years", "xbrli:pure"))
    if financial_unit("ratio", "") != "ratio":
        _fail(financial_unit("ratio", ""))
    if financial_unit("shares", "shares") != "shares":
        _fail("shares fact stays shares")


def test_financial_unit_jpy_facts_keep_jpy() -> None:
    if financial_unit("JPYPerShares", "JPYPerShares") != "JPYPerShares":
        _fail("bps JPY")
    if financial_unit("JPY", "JPY") != "JPY":
        _fail("rd/capex JPY")
    if financial_unit("JPY", "iso4217:JPY") != "iso4217:JPY":
        _fail("measure-style JPY")


def test_financial_unit_usd_facts_keep_usd() -> None:
    if financial_unit("JPYPerShares", "USDPerShares") != "USDPerShares":
        _fail("bps USD")
    if financial_unit("JPY", "USD") != "USD":
        _fail("rd/capex USD")
    if financial_unit(None, "USDPerShares") != "USDPerShares":
        _fail("eps has no std.unit")
    if financial_unit(None, None) != "JPY":
        _fail("fallback JPY")


def main() -> None:
    test_is_pure_unit()
    test_financial_unit_pure_uses_std()
    test_financial_unit_jpy_facts_keep_jpy()
    test_financial_unit_usd_facts_keep_usd()
    print("ok: xbrl financial unit")


if __name__ == "__main__":
    main()
