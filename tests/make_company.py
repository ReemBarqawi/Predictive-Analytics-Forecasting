"""
A made-up insurance company in the extract format of the data contract (pipeline/contract.py), for testing the
whole pipeline on data it has never seen. Each option switches one feature on or off, so a test can build the
company it needs (motor only, no vehicles, short history, one class, a missing year, other lines, ...).

    from make_company import make_company
    make_company(raw_dir, years=(2017, 2024), vehicles=True, triangles=True, lines=True)

Every amount is random but reproducible (seed). Nothing here comes from a real company.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

OFFICES = ["Riyadh Main Office", "Jeddah Branch", "Dammam Branch", "Abha Branch", "Tabuk Branch"]
MOTOR = {  # class: (code, frequency per car-year, mean claim, base premium per car, cars per policy)
    "Motor Private": (1, 0.12, 6000, 1400, (1, 1)),
    "Motor Commercial": (2, 0.25, 8000, 2600, (1, 20)),
    "Motor Rent a Car": (3, 0.35, 7000, 2200, (5, 60)),
}
TYPES = {"Comprehensive": (1, 1.0, 1.0), "Third Party": (2, 0.45, 0.55)}  # code, premium factor, cost factor
LINES = {  # department: {class: (code, policies a year, base premium, claims per policy, mean claim)}
    "FGA": {"Fire": (20, 300, 15000, 0.04, 120000), "Liabilities": (22, 1500, 900, 0.06, 6000)},
    "Marine": {"Marine Cargo": (10, 1200, 400, 0.03, 9000)},
    "Medical Dept": {"Group Medical": (51, 800, 5000, 0.0, 0)},   # policies, no claims (as real Medical)
}


def _vid(i: int) -> str:
    return hashlib.sha1(f"car{i}".encode()).hexdigest().upper()


def make_company(raw_dir: Path, years: tuple[int, int] = (2017, 2024), classes: tuple[str, ...] = tuple(MOTOR),
                 policies_per_year: int = 5000, vehicles: bool = True, triangles: bool = True, lines: bool = True,
                 skip_year: int | None = None, seed: int = 7) -> dict[str, pd.DataFrame]:
    """Write the extract files to raw_dir and return them. The data ends on 18 January after the last year, so
    the last year is the current year (as a real backup)."""
    rng = np.random.default_rng(seed)
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    cutoff = pd.Timestamp(years[1] + 1, 1, 18)
    pol_rows, veh_rows, clm_rows = [], [], []
    customers: list[dict] = []      # renewing customers keep their cars
    next_car, next_pol = 0, 0
    for year in range(years[0], years[1] + 2):
        if year == skip_year:
            customers = []
            continue
        n_new = policies_per_year - len(customers) if year <= years[1] else policies_per_year // 20
        new = []
        for _ in range(max(n_new, 0)):
            cls = classes[rng.integers(len(classes))]
            lo, hi = MOTOR[cls][4]
            ncar = int(rng.integers(lo, hi + 1))
            new.append({"cls": cls, "type": rng.choice(list(TYPES), p=[0.4, 0.6]), "cars": list(range(next_car, next_car + ncar)),
                        "office": OFFICES[rng.integers(len(OFFICES))], "source": rng.choice(["Direct", "Brokers"]),
                        "start": int(rng.integers(0, 365))})
            next_car += ncar
        book = customers + new
        renewing = []
        for c in book:
            code, freq, sev, base, _ = MOTOR[c["cls"]]
            tcode, pf, cf = TYPES[c["type"]]
            eff = pd.Timestamp(year, 1, 1) + pd.Timedelta(days=c["start"])
            if eff > cutoff + pd.Timedelta(days=90):
                continue
            exp = eff + pd.Timedelta(days=365)
            next_pol += 1
            office_no = OFFICES.index(c["office"]) + 1
            pno = f"{office_no}-1-{code}-{tcode}-{1 if c['source'] == 'Direct' else 2}-{next_pol}"
            ncar = len(c["cars"])
            prem = float(base * pf * ncar * rng.lognormal(0, 0.25) * (1.03 ** (year - years[0])))
            si = float(rng.choice([40000, 80000, 150000, 300000]) * ncar)
            pol_rows.append({"POLICY_NO": pno, "POLICY_YEAR": year, "CLASS_DESC": c["cls"], "POLICY_TYPE_DESC": c["type"],
                             "POLICY_EFFECTIVE_DATE": eff, "POLICY_EXPIRY_DATE": exp, "TOTAL_PREMIUM": round(prem, 2),
                             "COMMISSION": round(prem * 0.1, 2), "SOURCE_DESC": c["source"], "OFFICE_NAME": c["office"],
                             "SUM_INSURED": si, "CLASS_NO": code, "POLICY_TYPE_NO": tcode, "BRANCH_NO": office_no,
                             "BRANCH_NAME": c["office"], "AGENT_NO": float(rng.integers(1, 50)), "AGENT_NAME": "Agent",
                             "GROSS_PREMIUM": round(prem, 2), "NET_PREMIUM": round(prem, 2), "DEPARTMENT_NO": 1,
                             "DEPARTMENT_NAME": "Motor"})
            for car in c["cars"]:
                if vehicles:
                    veh_rows.append({"VEHICLE_ID": _vid(car), "POLICY_NO": pno, "POLICY_YEAR": year, "COVER_START": eff,
                                     "COVER_END": exp, "STATUS": 1.0, "STATUS_DATE": eff, "GROSS_PREMIUM": prem / ncar,
                                     "YEAR_OF_MAKE": float(year - rng.integers(0, 15)), "CLASS_OF_USE": 1.0,
                                     "VEHICLE_MAKE": 1.0, "BODY_TYPE": 1.0, "SUM_INSURED": si / ncar,
                                     "DRIVER_AGE": float(rng.integers(20, 70))})
                for _ in range(rng.poisson(freq * cf)):
                    acc = eff + pd.Timedelta(days=int(rng.integers(0, 365)))
                    if acc > cutoff:
                        continue
                    amt = float(rng.gamma(2.0, sev * cf / 2))
                    open_ = (cutoff - acc).days < 200 and rng.random() < 0.6
                    clm_rows.append({"CLM_NO": f"{pno}-{year}-{len(clm_rows) + 1}", "POLICY_NO": pno, "POLICY_YEAR": year,
                                     "ACC_DATE": acc, "TOTAL_PAID": round(amt * (0.3 if open_ else 1.0), 2),
                                     "TOTAL_EST": round(amt, 2), "CLOSE_DATE": pd.NaT if open_ else acc + pd.Timedelta(days=60),
                                     "CLM_YEAR": acc.year, "ACC_TYPE_DESC": "Collision", "RECOVERY_AMT": 0.0,
                                     "_COB": code, "_TYPE": tcode})
            if rng.random() < 0.6:
                renewing.append({**c, "start": c["start"]})
        customers = renewing
    files = {"policies": pd.DataFrame(pol_rows)}
    claims = pd.DataFrame(clm_rows)
    if triangles:
        tri = _triangles(claims, cutoff)       # the true development, from every claim's full amount
        files.update(tri)
        claims = _as_known_at(claims, cutoff, rng)
    files["claims"] = claims.drop(columns=["_COB", "_TYPE"])
    if vehicles:
        files["vehicles"] = pd.DataFrame(veh_rows)
    if lines:
        files.update(_lines(years, cutoff, rng))
    for name, df in files.items():
        df.to_parquet(raw_dir / f"{name}.parquet", index=False)
    return files


INCURRED_PATTERN = [0.55, 0.25, 0.12, 0.05, 0.03]   # share of a quarter's cost estimated in each development quarter
REPORTED_PATTERN = [0.8, 0.15, 0.05]                 # share of its claims reported in each development quarter


def _as_known_at(claims: pd.DataFrame, cutoff: pd.Timestamp, rng) -> pd.DataFrame:
    """The claims extract as a real backup holds it: recent claims partly not yet reported, and the reported ones
    estimated at the share their development quarter has reached (the same patterns as the triangles)."""
    age = (cutoff.to_period("Q") - 1 - claims["ACC_DATE"].dt.to_period("Q")).apply(lambda d: d.n).clip(lower=0)
    rep = np.cumsum(REPORTED_PATTERN)
    inc = np.cumsum(INCURRED_PATTERN)
    reported_share = age.map(lambda a: rep[min(a, len(rep) - 1)])
    keep = rng.random(len(claims)) < reported_share.to_numpy()
    c = claims[keep].copy()
    est_share = age[keep].map(lambda a: inc[min(a, len(inc) - 1)] / rep[min(a, len(rep) - 1)]).clip(upper=1.0)
    c["TOTAL_EST"] = (c["TOTAL_EST"] * est_share).round(2)
    c["TOTAL_PAID"] = c[["TOTAL_PAID", "TOTAL_EST"]].min(axis=1)
    return c


def _triangles(claims: pd.DataFrame, cutoff: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Incurred and reported-count triangles: each claim reported and estimated over its first quarters."""
    aq = claims["ACC_DATE"].dt.to_period("Q")
    last = cutoff.to_period("Q") - 1
    inc, rep = [], []
    for (q, cob, typ), g in claims.assign(AQ=aq).groupby(["AQ", "_COB", "_TYPE"]):
        for d, share in enumerate(INCURRED_PATTERN):
            if q + d > last:
                break
            inc.append({"ACC_QUARTER": q.start_time, "DEV_QUARTER": d, "COB": cob, "POLICY_TYPE": typ, "PARTY": "OWN",
                        "INCURRED_CHANGE": float(g["TOTAL_EST"].sum() * share)})
        for d, share in enumerate(REPORTED_PATTERN):
            if q + d > last:
                break
            rep.append({"ACC_QUARTER": q.start_time, "DEV_QUARTER": d, "COB": cob, "POLICY_TYPE": typ,
                        "CLAIMS_REPORTED": int(round(len(g) * share))})
    return {"claims_dev_incurred": pd.DataFrame(inc), "claims_dev_reported": pd.DataFrame(rep)}


def _lines(years: tuple[int, int], cutoff: pd.Timestamp, rng) -> dict[str, pd.DataFrame]:
    pol, clm = [], []
    n = 0
    dept_no = {"FGA": 3, "Marine": 2, "Medical Dept": 8}
    for dept, classes in LINES.items():
        for cls, (code, per_year, base, cpp, sev) in classes.items():
            for year in range(years[0], years[1] + 1):
                for _ in range(per_year):
                    n += 1
                    eff = pd.Timestamp(year, 1, 1) + pd.Timedelta(days=int(rng.integers(0, 365)))
                    prem = float(base * rng.lognormal(0, 0.6))
                    pno = f"1-{dept_no[dept]}-{code}-{code}-1-{n}"
                    pol.append({"POLICY_NO": pno, "POLICY_YEAR": year, "POLICY_NUMBER": n, "DEPARTMENT": dept,
                                "CLASS_NO": code, "CLASS_DESC": cls, "POLICY_TYPE_DESC": cls, "SOURCE_DESC": "Brokers",
                                "OFFICE_NAME": OFFICES[rng.integers(len(OFFICES))], "POLICY_EFFECTIVE_DATE": eff,
                                "POLICY_EXPIRY_DATE": eff + pd.Timedelta(days=365), "TOTAL_PREMIUM": round(prem, 2),
                                "GROSS_PREMIUM": round(prem, 2), "NET_PREMIUM": round(prem, 2),
                                "COMMISSION": round(prem * 0.12, 2), "SUM_INSURED": prem * 500, "ROWS_IN_SOURCE": 1})
                    for _ in range(rng.poisson(cpp)):
                        acc = eff + pd.Timedelta(days=int(rng.integers(0, 365)))
                        if acc > cutoff:
                            continue
                        amt = float(rng.gamma(1.2, sev / 1.2))
                        clm.append({"CLM_NO": f"{pno}-{len(clm) + 1}", "POLICY_NO": pno, "POLICY_YEAR": year,
                                    "POLICY_NUMBER": n, "DEPARTMENT": dept, "CLASS_NO": code, "CLASS_DESC": cls,
                                    "POLICY_TYPE_DESC": cls, "ACC_DATE": acc, "CLOSE_DATE": pd.NaT, "CLM_YEAR": acc.year,
                                    "ACC_TYPE_DESC": "Loss", "TOTAL_EST": round(amt, 2), "TOTAL_PAID": round(amt * 0.5, 2),
                                    "RECOVERY_AMT": 0})
    claims = pd.DataFrame(clm, columns=["CLM_NO", "POLICY_NO", "POLICY_YEAR", "POLICY_NUMBER", "DEPARTMENT", "CLASS_NO",
                                        "CLASS_DESC", "POLICY_TYPE_DESC", "ACC_DATE", "CLOSE_DATE", "CLM_YEAR",
                                        "ACC_TYPE_DESC", "TOTAL_EST", "TOTAL_PAID", "RECOVERY_AMT"])
    return {"lines_policies": pd.DataFrame(pol), "lines_claims": claims}
