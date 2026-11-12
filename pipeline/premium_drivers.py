"""
pipeline/premium_drivers.py
===========================
Written premium split into renewals and new business, for the driver premium model (plan B1).

A vehicle-policy is a renewal when the same car (VEHICLE_ID, hashed chassis) was insured with the
company before and the new cover starts from RENEW_BEFORE_DAYS before to RENEW_AFTER_DAYS after the
old one ended (the window of step 17). A policy's renewal share = the gross premium of its renewed
vehicles / the gross premium of all its vehicles; its written premium is split by that share.

Forecast at an origin o, month m after it:
    renewals(m)     = premium of the policies expiring in m (known at o) x the segment's renewal
                      premium ratio (renewal premium written / premium that expired, over the 12
                      observable months before o: it covers both how many renew and their price change)
    new business(m) = forecast from the new-business history up to o (the caller's model)

Needs fact_vehicle.parquet (sql/vehicles.sql); without it the caller keeps its total forecast.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from settings import S

SEG = ["ZONE", "CLASS_DESC", "POLICY_TYPE_DESC"]
RENEW_BEFORE_DAYS = 30      # same window as 17_renewal.py
RENEW_AFTER_DAYS = 60
# Plan B2 (method constant): a sales channel (SOURCE_DESC) is forecast on its own when it brought at
# least this share of new business in the 12 months before the origin; smaller ones are grouped.
CHANNEL_MIN_SHARE = 0.05


class Drivers:
    def __init__(self, classes: list[str] | None = None, by_channel: bool = False):
        path = S.facts_dir / "fact_vehicle.parquet"
        self.available = path.exists()
        self.by_channel = by_channel        # plan B2: forecast new business per sales channel
        if not self.available:
            return
        v = pd.read_parquet(path, columns=["VEHICLE_ID", "POLICY_NO", "POLICY_YEAR", "COVER_START", "COVER_END",
                                           "GROSS_PREMIUM"]).sort_values(["VEHICLE_ID", "COVER_START", "POLICY_YEAR"])
        gap = (v["COVER_START"] - v.groupby("VEHICLE_ID")["COVER_END"].shift(1)).dt.days
        v["RENEWAL"] = gap.between(-RENEW_BEFORE_DAYS, RENEW_AFTER_DAYS)
        # vehicles first seen in the extract's first year cannot be told apart (no earlier policy in it)
        self.first_classified = (v["COVER_START"].min() + pd.DateOffset(years=1)).to_period("M")
        g = v.assign(RG=v["GROSS_PREMIUM"].clip(lower=0) * v["RENEWAL"],
                     G=v["GROSS_PREMIUM"].clip(lower=0)).groupby(["POLICY_NO", "POLICY_YEAR"])[["RG", "G"]].sum()
        share = (g["RG"] / g["G"].replace(0, np.nan)).fillna(0.0).rename("RENEWAL_SHARE").reset_index()

        p = pd.read_csv(S.facts_dir / "fact_policy.csv",
                        usecols=["POLICY_NO", "POLICY_YEAR", "POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE",
                                 "TOTAL_PREMIUM", "SOURCE_DESC"] + SEG,
                        parse_dates=["POLICY_EFFECTIVE_DATE", "POLICY_EXPIRY_DATE"],
                        low_memory=False)
        if classes is not None:
            p = p[p["CLASS_DESC"].isin(classes)]
        p = p[p["POLICY_EXPIRY_DATE"] > p["POLICY_EFFECTIVE_DATE"]].merge(share, on=["POLICY_NO", "POLICY_YEAR"], how="left")
        p["RENEWAL_SHARE"] = p["RENEWAL_SHARE"].fillna(0.0)
        p["W"] = p["POLICY_EFFECTIVE_DATE"].dt.to_period("M")
        p["E"] = p["POLICY_EXPIRY_DATE"].dt.to_period("M")
        p["RENEWAL_PREMIUM"] = p["TOTAL_PREMIUM"] * p["RENEWAL_SHARE"]
        p["SOURCE_DESC"] = p["SOURCE_DESC"].fillna("Unknown")
        self.p = p
        self.renewal_written = p.groupby("W")["RENEWAL_PREMIUM"].sum()
        self.nb_by_channel = (p.assign(NB=p["TOTAL_PREMIUM"] - p["RENEWAL_PREMIUM"])
                               .groupby(["W", "SOURCE_DESC"])["NB"].sum().unstack(fill_value=0.0))

    def new_business_channels(self, index: pd.PeriodIndex) -> pd.DataFrame:
        """Plan B2: new business per sales channel over `index` (from the first month renewals can be
        told). Channels under CHANNEL_MIN_SHARE of the last 12 months are summed as "Other"."""
        nb = self.nb_by_channel.reindex(index, fill_value=0.0).loc[self.first_classified:].clip(lower=0.0)
        share = nb.iloc[-12:].sum() / max(nb.iloc[-12:].sum().sum(), 1e-9)
        main = share[share >= CHANNEL_MIN_SHARE].index.tolist()
        out = nb[main].copy()
        rest = nb.drop(columns=main).sum(axis=1)
        if rest.sum() > 0:
            out["Other"] = rest
        return out

    def new_business(self, written: pd.Series) -> pd.Series:
        """New-business history = written premium - renewals, from the first month renewals can be told."""
        nb = written - self.renewal_written.reindex(written.index, fill_value=0.0)
        return nb.loc[self.first_classified:].clip(lower=0.0)

    def renewal_ratio(self, origin: pd.Period) -> pd.Series:
        """Per segment: renewal premium written / premium that expired, over the 12 months whose
        renewals are observable at the origin (renewals may start up to RENEW_AFTER_DAYS late)."""
        end = origin - (RENEW_AFTER_DAYS + 29) // 30
        start = end - 11
        p = self.p
        expired = p[(p["E"] >= start) & (p["E"] <= end) & (p["W"] <= origin)].groupby(SEG)["TOTAL_PREMIUM"].sum()
        renewed = p[(p["W"] >= start) & (p["W"] <= end)].groupby(SEG)["RENEWAL_PREMIUM"].sum()
        both = pd.concat([expired.rename("EXP"), renewed.rename("REN")], axis=1).fillna(0.0)
        portfolio = both["REN"].sum() / both["EXP"].sum() if both["EXP"].sum() > 0 else 0.0
        # segments with little expiring premium lean on the portfolio ratio
        z = np.minimum(1.0, both["EXP"] / max(both["EXP"].median(), 1.0))
        own = (both["REN"] / both["EXP"].replace(0, np.nan)).fillna(portfolio)
        return (z * own + (1 - z) * portfolio).rename("RATIO")

    def renewals(self, origin: pd.Period, months: pd.PeriodIndex) -> pd.Series:
        """Expected renewal premium written in each month after the origin, from the policies in
        force at the origin that expire in that month."""
        p = self.p[(self.p["W"] <= origin) & self.p["E"].isin(months)]
        ratio = self.renewal_ratio(origin)
        exp = p.groupby(SEG + ["E"])["TOTAL_PREMIUM"].sum().reset_index()
        exp = exp.merge(ratio.reset_index(), on=SEG, how="left")
        exp["RATIO"] = exp["RATIO"].fillna(float(ratio.mean()) if len(ratio) else 0.0)
        return (exp["TOTAL_PREMIUM"] * exp["RATIO"]).groupby(exp["E"]).sum().reindex(months, fill_value=0.0)
