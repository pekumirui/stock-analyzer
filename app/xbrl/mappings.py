"""Load std_items / element mappings from YAML."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.models import XbrlElementMapping, XbrlStdItem

DEFAULT_CONFIG_DIR = BASE_DIR / "config" / "xbrl"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def load_std_items_spec(config_dir: Path | None = None) -> list[dict]:
    root = DEFAULT_CONFIG_DIR if config_dir is None else config_dir
    data = load_yaml(root / "std_items.yaml")
    if not isinstance(data, list):
        raise ValueError("std_items.yaml must be a list")
    return data


def load_mapping_specs(config_dir: Path | None = None) -> list[dict]:
    root = DEFAULT_CONFIG_DIR if config_dir is None else config_dir
    mapping_dir = root / "mappings"
    rows: list[dict] = []
    if not mapping_dir.is_dir():
        return rows
    for path in sorted(mapping_dir.glob("*.yaml")):
        data = load_yaml(path)
        if not isinstance(data, list):
            raise ValueError(f"{path.name} must be a list")
        rows.extend(data)
    return rows


def apply_mappings(db: Session, config_dir: Path | None = None) -> dict[str, int]:
    """UPSERT YAML defs and delete mappings that disappeared from YAML."""
    std_specs = load_std_items_spec(config_dir)
    map_specs = load_mapping_specs(config_dir)

    kept_std: set[str] = set()
    for i, item in enumerate(std_specs):
        code = str(item["code"])
        kept_std.add(code)
        row = db.query(XbrlStdItem).filter_by(code=code).one_or_none()
        if row is None:
            row = XbrlStdItem(code=code)
            db.add(row)
        row.statement = str(item["statement"])
        row.section = item.get("section")
        row.label_ja = str(item["label_ja"])
        row.display_order = int(item.get("display_order", i * 10))
        row.sign = str(item.get("sign") or "natural")
        row.is_required = bool(item.get("is_required", False))
    db.flush()

    wanted: set[tuple[int, str, str, str | None]] = set()
    created = 0
    for spec in map_specs:
        std_code = str(spec["std_item"])
        std = db.query(XbrlStdItem).filter_by(code=std_code).one_or_none()
        if std is None:
            raise ValueError(f"unknown std_item in mapping: {std_code}")
        for el in spec.get("elements") or []:
            ns = str(el["ns"])
            name = str(el["name"])
            standard = el.get("standard")
            key = (std.id, ns, name, standard)
            wanted.add(key)
            row = (
                db.query(XbrlElementMapping)
                .filter_by(
                    std_item_id=std.id,
                    namespace_key=ns,
                    local_name=name,
                    accounting_standard=standard,
                )
                .one_or_none()
            )
            if row is None:
                row = XbrlElementMapping(
                    std_item_id=std.id,
                    namespace_key=ns,
                    local_name=name,
                    accounting_standard=standard,
                )
                db.add(row)
                created += 1
            row.priority = int(el.get("priority", 100))
            row.note = el.get("note")

    stale = db.query(XbrlElementMapping).all()
    deleted = 0
    for row in stale:
        key = (row.std_item_id, row.namespace_key, row.local_name, row.accounting_standard)
        if key not in wanted:
            db.delete(row)
            deleted += 1
    db.commit()
    return {"std_items": len(kept_std), "mappings": len(wanted), "created": created, "deleted": deleted}
