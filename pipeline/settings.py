"""
settings.py — one place where every pipeline step gets its folders and settings.

Reads config.toml (committed defaults) and config.local.toml (git-ignored, per machine),
then picks the profile for the active data source. The source comes from, in order:
the AMAN_SOURCE environment variable (set by run_all.py --source), else [run] source.

Usage inside a step:
    from settings import S
    S.facts_dir / "fact_policy.csv"
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ("synthetic", "oracle")


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config() -> dict:
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    # AMAN_LOCAL_CONFIG points at another local file (used by tests); default config.local.toml
    local = Path(os.environ.get("AMAN_LOCAL_CONFIG") or ROOT / "config.local.toml")
    if local.exists():
        cfg = _merge(cfg, tomllib.loads(local.read_text(encoding="utf-8")))
    return cfg


def _path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


@dataclass(frozen=True)
class Settings:
    source: str
    output_root: Path
    facts_dir: Path
    dims_dir: Path
    models_dir: Path
    reports_dir: Path
    raw_dir: Path | None
    motor_classes: list[str]
    config: dict

    def ensure_dirs(self) -> None:
        for d in (self.facts_dir, self.dims_dir, self.models_dir, self.reports_dir, self.raw_dir):
            if d is not None:
                d.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    cfg = load_config()
    source = os.environ.get("AMAN_SOURCE") or cfg.get("run", {}).get("source", "synthetic")
    if source not in SOURCES:
        raise SystemExit(f"Unknown data source '{source}'. Use one of: {', '.join(SOURCES)}")
    prof = cfg["profiles"][source]
    root = _path(prof["output_root"])
    s = Settings(
        source=source,
        output_root=root,
        facts_dir=root / "02_facts",
        dims_dir=root / "03_dims",
        models_dir=_path(prof["models_dir"]),
        reports_dir=_path(prof["reports_dir"]),
        raw_dir=_path(prof["raw_dir"]) if "raw_dir" in prof else None,
        motor_classes=list(cfg.get("data", {}).get("motor_classes", [])),
        config=cfg,
    )
    s.ensure_dirs()
    return s


S = get_settings()


def is_motor(series):
    """Boolean mask for motor business; keeps every row when no class list is configured."""
    return series.isin(S.motor_classes) if S.motor_classes else series.notna() | series.isna()
