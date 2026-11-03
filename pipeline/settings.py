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
from datetime import date
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


def valuation_date() -> date:
    """The run's as-of date: [prepare] as_of_date if set, else the date of the warehouse claims
    extract, else today (synthetic data has no extract)."""
    cfg = S.config.get("prepare", {}).get("as_of_date") or ""
    if cfg:
        return date.fromisoformat(cfg)
    extract = S.raw_dir / "claims.parquet" if S.raw_dir else None
    if extract is not None and extract.exists():
        return date.fromtimestamp(extract.stat().st_mtime)
    return date.today()


def current_year():
    """The data cut-off and the current policy year: the last calendar year the claims reach to its
    end (claims to 18 Jan 2025 -> 2024). Every step that reports "the current year" uses this, so
    the dashboard pages describe the same book. Returns (cutoff Timestamp, year)."""
    import pandas as pd
    acc = pd.read_csv(S.facts_dir / "fact_claim.csv", usecols=["ACC_DATE"], parse_dates=["ACC_DATE"])["ACC_DATE"]
    cutoff = acc.max()
    if pd.isna(cutoff):
        raise SystemExit("fact_claim.csv has no accident dates: cannot tell which year is current.")
    return cutoff, cutoff.year if (cutoff.month, cutoff.day) == (12, 31) else cutoff.year - 1


def is_motor(series):
    """Boolean mask for motor business; keeps every row when no class list is configured."""
    return series.isin(S.motor_classes) if S.motor_classes else series.notna() | series.isna()


# Rating factors known at quote time (no target leakage), used by every pricing GLM.
GENERAL_RATING_FACTORS = ["POLICY_TYPE_DESC", "CLASS_DESC", "SOURCE_DESC", "ZONE", "SUM_INSURED_BAND"]
# Protected factors: in pricing only when [pricing] in config.toml switches them on.
PROTECTED_RATING_FACTORS = {"use_gender": "GENDER", "use_nationality": "NATIONALITY"}


def pricing_features() -> list[str]:
    """General rating factors, plus each protected factor the manager has allowed."""
    cfg = S.config.get("pricing", {})
    return GENERAL_RATING_FACTORS + [col for key, col in PROTECTED_RATING_FACTORS.items() if cfg.get(key)]
