"""
00_extract_oracle.py — pull policies and claims from the Oracle warehouse.

    python pipeline/00_extract_oracle.py --check   # connect and count rows only (no data saved)
    python pipeline/00_extract_oracle.py           # extract to <raw_dir>/<name>.parquet

Runs every .sql file in [oracle] sql_dir (policies, claims, claims_dev_*, vehicles, and lines_policies /
lines_claims for the other lines of business). Connection details come
from config.local.toml ([oracle] dsn, user). The password is read from the environment
variable AMAN_ORACLE_PASSWORD, or asked for at the prompt; it is never written anywhere.

Uses python-oracledb in thin mode: no Oracle client installation needed.
Only row counts and column names are printed — never data values.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from settings import ROOT, S


def connect():
    try:
        import oracledb
    except ImportError:
        sys.exit("Missing package: run  pip install -r requirements.txt")
    ora = S.config.get("oracle", {})
    dsn, user = (ora.get("dsn") or "").strip(), (ora.get("user") or "").strip()
    # Unfilled = still the example values. A full TCP descriptor legitimately contains
    # "(SERVICE_NAME=...)", so only the short-form placeholder ".../SERVICE_NAME" counts.
    placeholder = dsn.upper().endswith("/SERVICE_NAME") or user == "READONLY_USER"
    if not dsn or not user or placeholder:
        sys.exit("Set [oracle] dsn and user in config.local.toml "
                 "(copy config.local.example.toml).")
    password = os.environ.get("AMAN_ORACLE_PASSWORD") or getpass.getpass(f"Oracle password for {user}: ")
    try:
        return oracledb.connect(user=user, password=password, dsn=dsn)
    except oracledb.Error as e:
        # The message is Oracle's error code/text; it contains no password.
        sys.exit(f"Could not connect: {e}")


def sql_files() -> list[Path]:
    folder = ROOT / S.config.get("oracle", {}).get("sql_dir", "sql")
    files = sorted(folder.glob("*.sql"))
    if not files:
        sys.exit(f"No .sql files in {folder}")
    return files


def read_sql(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip().rstrip(";")
    if "SCHEMA_NAME." in text:
        sys.exit(f"{path.name} is still the template: replace SCHEMA_NAME/table/column names.")
    # The models learn from every policy year the warehouse holds unless [oracle] first_policy_year
    # says otherwise; Power BI still shows only the last [report] years (step 12).
    first = S.config.get("oracle", {}).get("first_policy_year", 0)
    if not isinstance(first, int) or first < 0:
        sys.exit("[oracle] first_policy_year in config.toml must be a whole year, or 0 for all history.")
    vehicles = S.config.get("oracle", {}).get("vehicles_years", 4)
    if not isinstance(vehicles, int) or vehicles < 1:
        sys.exit("[oracle] vehicles_years in config.toml must be a whole number of years, 1 or more.")
    return text.replace("{first_policy_year}", str(first)).replace("{vehicles_years}", str(vehicles))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="store_true", help="connect and count rows only")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="extract only these sql files (names without .sql), e.g. --only vehicles")
    args = ap.parse_args()

    if S.source != "oracle":
        print("Note: active source is 'synthetic'; extracting anyway into the oracle raw folder.")
    raw_dir = S.raw_dir or (ROOT / "data" / "raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    batch = int(S.config.get("oracle", {}).get("fetch_batch_rows", 50000))

    conn = connect()
    print(f"Connected (Oracle {conn.version}).")
    manifest_path = raw_dir / "extract_manifest.json"
    manifest = {"extracted_at": datetime.now().isoformat(timespec="seconds"), "tables": {}}
    if args.only and manifest_path.exists():      # keep the other tables' entries
        manifest["tables"] = json.loads(manifest_path.read_text(encoding="utf-8")).get("tables", {})
    files = [f for f in sql_files() if not args.only or f.stem in args.only]
    if args.only and len(files) < len(args.only):
        sys.exit(f"Unknown sql file in --only: {sorted(set(args.only) - {f.stem for f in files})}")
    with conn:
        for f in files:
            name, sql = f.stem, read_sql(f)
            cur = conn.cursor()
            if args.check:
                # newlines: a query may end with a -- comment, which would swallow the closing bracket
                cur.execute(f"SELECT COUNT(*) FROM (\n{sql}\n)")
                print(f"  {name:<12} {cur.fetchone()[0]:>12,} rows available")
                continue
            t0 = time.time()
            cur.arraysize = batch
            cur.execute(sql)
            cols = [d[0].upper() for d in cur.description]
            chunks, n = [], 0
            while True:
                rows = cur.fetchmany(batch)
                if not rows:
                    break
                chunks.append(pd.DataFrame(rows, columns=cols))
                n += len(rows)
                print(f"  {name}: {n:,} rows...", end="\r", flush=True)
            df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=cols)
            out = raw_dir / f"{name}.parquet"
            df.to_parquet(out, index=False)
            manifest["tables"][name] = {
                "rows": len(df), "columns": cols, "file": out.name,
                "sql_sha1": hashlib.sha1(sql.encode()).hexdigest()[:12],
                "seconds": round(time.time() - t0, 1)}
            print(f"  {name:<12} {len(df):>12,} rows  {len(cols)} columns  -> {out}")
    if not args.check:
        manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
