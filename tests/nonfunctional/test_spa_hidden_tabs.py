from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_earnings_uses_abort_and_sized_pin() -> None:
    src = (ROOT / "static/js/earnings.js").read_text(encoding="utf-8")
    assert "AbortController" in src
    assert "beginDetail" in src
    assert "detailReq" not in src
    assert "earnSearchReq" not in src
    assert "タブが隠れている間は幅が 0" not in src
    assert "古いレスポンスで上書きしない" not in src


def test_supply_fits_when_sized_without_maxzoom() -> None:
    src = (ROOT / "static/js/supply.js").read_text(encoding="utf-8")
    assert "supplyFitMap" in src
    assert "supplyFitChart" in src
    assert "SUPPLY_MAX_ZOOM" not in src
    assert "maxZoom" not in src
    assert "display:none" not in src
    assert "大きさ 0" not in src
    assert "幅 0" not in src
