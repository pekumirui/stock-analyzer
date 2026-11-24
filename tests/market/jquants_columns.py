from __future__ import annotations

FY_COLUMNS = {"fy_net_sales": "FSales", "fy_operating_profit": "FOP", "fy_ordinary_profit": "FOdP",
              "fy_profit": "FNP", "fy_eps": "FEPS"}


def fy_columns(raw: dict) -> dict[str, float | None]:
    return {col: float(raw[key]) if raw.get(key) not in (None, "") else None for col, key in FY_COLUMNS.items()}
