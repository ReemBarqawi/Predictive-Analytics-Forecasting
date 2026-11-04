"""
06_lever_allocation.py
======================
Splits each segment's combined-ratio gap across the 5 profitability levers
(Risk-Based Repricing, Bonus-Malus, UW Controls, Claims Management, Distribution & Mix).

Reads : 02_facts/fact_segment_performance_by_year.csv  (current policy year only)
Writes: 02_facts/fact_lever_allocation.csv

The baseline is the current policy year (settings.current_year), the same book as the
Profitability page's default year and the optimizer, so every page starts from one combined
ratio. The all-years fact_segment_performance.csv summed every policy year in the extract
(including years outside the report window and the part-year after the cut-off).

Lever values are raw (unscaled); the proportional "never overshoot" allocation is done in DAX.
"""
import os
import pandas as pd

# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────
from settings import S, is_motor, current_year  # folders + motor classes for the active data source
FACTS_DIR  = str(S.facts_dir)

INPUT_PATH  = os.path.join(FACTS_DIR, "fact_segment_performance_by_year.csv")
OUTPUT_PATH = os.path.join(FACTS_DIR, "fact_lever_allocation.csv")

TARGET_CR = S.config["business"]["target_combined_ratio"]
L = S.config["levers"]


def aggregate_segments(df):
    """
    Collapse any duplicate Zone × Class × Policy Type rows into one.
    Sums the additive columns, then recalculates ratios from totals.
    This fixes the multi-row-per-segment problem.
    """
    # Step 1: sum all additive columns
    agg = df.groupby(
        ['ZONE', 'CLASS_DESC', 'POLICY_TYPE_DESC'], as_index=False
    ).agg(
        EARNED_PREMIUM   = ('EARNED_PREMIUM',   'sum'),
        ULTIMATE_CLAIMS  = ('ULTIMATE_CLAIMS',  'sum'),
        CLAIM_COUNT      = ('CLAIM_COUNT',       'sum'),
        EXPOSURE_YEARS   = ('EXPOSURE_YEARS',    'sum'),
        COMMISSION_SUM   = ('COMMISSION_SUM',    'sum'),
        ADMIN_EXPENSE    = ('ADMIN_EXPENSE',     'sum'),
        UW_PROFIT        = ('UW_PROFIT',         'sum'),
        BREAK_EVEN_TOTAL = ('BREAK_EVEN_TOTAL',  'sum'),
        POLICY_COUNT     = ('POLICY_COUNT',      'sum'),
        # For string columns take the first value
        SAFE_ZONE_STATUS = ('SAFE_ZONE_STATUS',  'first'),
        CREDIBILITY_FLAG = ('CREDIBILITY_FLAG',  'first'),
    )

    # Step 2: recalculate ratios from aggregated totals (never average ratios)
    ep   = agg['EARNED_PREMIUM']
    uc   = agg['ULTIMATE_CLAIMS']
    comm = agg['COMMISSION_SUM']
    adm  = agg['ADMIN_EXPENSE']
    cc   = agg['CLAIM_COUNT']
    expy = agg['EXPOSURE_YEARS']
    pc   = agg['POLICY_COUNT']

    agg['LOSS_RATIO']      = (uc / ep).where(ep > 0, 0)
    agg['EXPENSE_RATIO']   = ((comm + adm) / ep).where(ep > 0, 0)
    agg['COMBINED_RATIO']  = ((uc + comm + adm) / ep).where(ep > 0, 0)
    agg['PURE_PREMIUM_PER_YEAR'] = (uc / expy).where(expy > 0, 0)
    agg['AVG_PREMIUM_PER_POLICY'] = (ep / pc).where(pc > 0, 0)
    agg['MAX_SAFE_DISCOUNT'] = (1 - agg['BREAK_EVEN_TOTAL'] / ep).where(ep > 0, 0)

    # Step 3: drop segments with no written premium — nothing to reprice
    agg = agg[agg['EARNED_PREMIUM'] > 0].reset_index(drop=True)

    print(f"  After aggregation: {len(agg)} unique segments "
          f"(dropped {len(df) - len(agg)} rows)")
    return agg


def build_lever_allocation(seg_perf_df):
    """
    For each segment, compute how much each of the 5 levers
    can contribute to closing its combined ratio gap.
    All numbers derived from real segment data.
    """
    rows = []

    for _, seg in seg_perf_df.iterrows():
        zone = seg['ZONE']
        cls  = seg['CLASS_DESC']
        pol  = seg['POLICY_TYPE_DESC']

        ep      = seg['EARNED_PREMIUM']
        uc      = seg['ULTIMATE_CLAIMS']
        cc      = seg['CLAIM_COUNT']
        exp_yrs = seg['EXPOSURE_YEARS']
        comm    = seg['COMMISSION_SUM']
        admin   = seg['ADMIN_EXPENSE']
        cr      = seg['COMBINED_RATIO']
        pp      = seg['PURE_PREMIUM_PER_YEAR']
        avg_prem = seg['AVG_PREMIUM_PER_POLICY']
        msd     = seg['MAX_SAFE_DISCOUNT']

        gap_cr  = max(0, cr - TARGET_CR)
        gap_sar = max(0, (uc + comm + admin) - (ep * TARGET_CR))

        # ── Segment already profitable — all levers = 0 ──────────────
        if gap_cr == 0:
            rows.append({
                'ZONE': zone, 'CLASS_DESC': cls, 'POLICY_TYPE_DESC': pol,
                'gap_cr_pts': 0, 'gap_sar': 0,
                'lever_repricing_cr_pts': 0,  'lever_repricing_sar': 0,
                'lever_bonusmalus_cr_pts': 0, 'lever_bonusmalus_sar': 0,
                'lever_uw_controls_cr_pts': 0,'lever_uw_controls_sar': 0,
                'lever_claims_mgmt_cr_pts': 0,'lever_claims_mgmt_sar': 0,
                'lever_distribution_cr_pts': 0,'lever_distribution_sar': 0,
                'after_actions_cr': cr,
                'after_actions_sar': 0,
                'baseline_cr': cr,
                'baseline_earned_premium': ep,
                'baseline_ultimate_claims': uc,
                'baseline_claim_count': int(cc),
                'baseline_exposure_years': exp_yrs,
                'baseline_avg_severity': uc / cc if cc > 0 else 0,
                'baseline_frequency': cc / exp_yrs if exp_yrs > 0 else 0,
                'pricing_adequacy': avg_prem / pp if pp > 0 else 999,
                'safe_zone_status': seg['SAFE_ZONE_STATUS'],
                'credibility_flag': seg['CREDIBILITY_FLAG'],
            })
            continue

        # ── LEVER 1: Risk-Based Repricing ────────────────────────────
        # Source: AVG_PREMIUM_PER_POLICY vs PURE_PREMIUM_PER_YEAR
        # If avg charged < pure premium needed → segment is underpriced
        adequacy = avg_prem / pp if pp > 0 else 999
        if adequacy < 1.0:
            reprice_pct = min((1.0 / adequacy) - 1.0, L["reprice_cap"])
            reprice_sar = ep * reprice_pct
            reprice_cr_pts = reprice_pct          # = reprice_sar / ep
        else:
            reprice_sar, reprice_cr_pts = 0.0, 0.0

        # ── LEVER 2: Bonus-Malus ─────────────────────────────────────
        # Source: claim frequency (CLAIM_COUNT / EXPOSURE_YEARS)
        # High-frequency segments have more repeat claimants to surcharge
        freq = cc / exp_yrs if exp_yrs > 0 else 0
        bm_pct = min(freq * L["bonus_malus_share"], L["bonus_malus_cap"])
        bm_sar = ep * bm_pct
        bm_cr_pts = bm_pct

        # ── LEVER 3: UW Controls ─────────────────────────────────────
        # Source: average severity (ULTIMATE_CLAIMS / CLAIM_COUNT)
        # Preferred garage routing: severity reduction x realisation
        avg_sev = uc / cc if cc > 0 else 0
        uw_sar = uc * L["uw_severity_reduction"] * L["uw_realisation"]
        uw_cr_pts = uw_sar / ep if ep > 0 else 0

        # ── LEVER 4: Claims Management ───────────────────────────────
        # Source: ULTIMATE_CLAIMS (share recoverable via faster settlement)
        cm_sar = uc * L["claims_mgmt_recovery"]
        cm_cr_pts = cm_sar / ep if ep > 0 else 0

        # ── LEVER 5: Distribution & Mix ──────────────────────────────
        # Source: SAFE_ZONE_STATUS — Loss segments close part of their gap by mix shift
        if seg['SAFE_ZONE_STATUS'] == 'Loss':
            mix_sar = gap_sar * L["mix_share_of_gap"]
            mix_cr_pts = mix_sar / ep if ep > 0 else 0
        else:
            mix_sar, mix_cr_pts = 0.0, 0.0

        # ─── REMOVE THIS BLOCK ───
        # total_lever_sar = reprice_sar + bm_sar + uw_sar + cm_sar + mix_sar
        # if total_lever_sar > gap_sar and total_lever_sar > 0:
        #     scale = gap_sar / total_lever_sar
        #     ...

        # ─── INSTEAD, store raw unscaled values ───
        after_cr = cr  # no pre-calculation — DAX handles it

        rows.append({
            'ZONE': zone, 'CLASS_DESC': cls, 'POLICY_TYPE_DESC': pol,
            'gap_cr_pts': gap_cr,
            'gap_sar': gap_sar,
            'lever_repricing_cr_pts': reprice_cr_pts,
            'lever_repricing_sar': reprice_sar,
            'lever_bonusmalus_cr_pts': bm_cr_pts,
            'lever_bonusmalus_sar': bm_sar,
            'lever_uw_controls_cr_pts': uw_cr_pts,
            'lever_uw_controls_sar': uw_sar,
            'lever_claims_mgmt_cr_pts': cm_cr_pts,
            'lever_claims_mgmt_sar': cm_sar,
            'lever_distribution_cr_pts': mix_cr_pts,
            'lever_distribution_sar': mix_sar,
            'after_actions_cr': cr,
            'after_actions_sar': 0,
            'baseline_cr': cr,
            'baseline_earned_premium': ep,
            'baseline_ultimate_claims': uc,
            'baseline_claim_count': int(cc),
            'baseline_exposure_years': exp_yrs,
            'baseline_avg_severity': avg_sev,
            'baseline_frequency': freq,
            'pricing_adequacy': adequacy,
            'safe_zone_status': seg['SAFE_ZONE_STATUS'],
            'credibility_flag': seg['CREDIBILITY_FLAG'],
        })


    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("Loading fact_segment_performance...")
    seg_perf_df = pd.read_csv(INPUT_PATH)
    _, year = current_year()
    seg_perf_df = seg_perf_df[seg_perf_df['POLICY_YEAR'] == year]
    print(f"  Policy year {year}: {len(seg_perf_df)} rows")

    # FIX 1: collapse duplicates + drop zero-premium rows
    print("Aggregating segments...")
    seg_perf_df = aggregate_segments(seg_perf_df)

    # Build lever allocation
    print("Building lever allocation...")
    lever_df = build_lever_allocation(seg_perf_df)

    # Save
    os.makedirs(FACTS_DIR, exist_ok=True)
    lever_df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nDone — saved {len(lever_df)} rows to:\n  {OUTPUT_PATH}")

    # Quick sanity check
    print("\n── Sanity check ──────────────────────────────────")
    print(f"  Segments with repricing action : "
          f"{(lever_df['lever_repricing_sar'] > 0).sum()}")
    print(f"  Segments already profitable    : "
          f"{(lever_df['gap_cr_pts'] == 0).sum()}")
    print(f"  Total repricing SAR            : "
          f"{lever_df['lever_repricing_sar'].sum():,.0f}")
    print(f"  Total gap SAR                  : "
          f"{lever_df['gap_sar'].sum():,.0f}")
    print(f"  Avg after-actions CR           : "
          f"{lever_df['after_actions_cr'].mean():.4f}")