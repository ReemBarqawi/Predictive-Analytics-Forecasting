import os
import pandas as pd

# ─────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR   = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
FACTS_DIR  = os.path.join(BASE_DIR, "SYNTHETIC_DSS_DATA", "02_facts")

INPUT_PATH  = os.path.join(FACTS_DIR, "fact_segment_performance.csv")
OUTPUT_PATH = os.path.join(FACTS_DIR, "fact_lever_allocation.csv")

TARGET_CR = 0.85  # target combined ratio


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
                'after_actions_cr': round(cr, 4),
                'after_actions_sar': 0,
                'baseline_cr': round(cr, 4),
                'baseline_earned_premium': round(ep, 2),
                'baseline_ultimate_claims': round(uc, 2),
                'baseline_claim_count': int(cc),
                'baseline_exposure_years': round(exp_yrs, 2),
                'baseline_avg_severity': round(uc / cc if cc > 0 else 0, 2),
                'baseline_frequency': round(cc / exp_yrs if exp_yrs > 0 else 0, 4),
                'pricing_adequacy': round(avg_prem / pp if pp > 0 else 999, 4),
                'safe_zone_status': seg['SAFE_ZONE_STATUS'],
                'credibility_flag': seg['CREDIBILITY_FLAG'],
            })
            continue

        # ── LEVER 1: Risk-Based Repricing ────────────────────────────
        # Source: AVG_PREMIUM_PER_POLICY vs PURE_PREMIUM_PER_YEAR
        # If avg charged < pure premium needed → segment is underpriced
        adequacy = avg_prem / pp if pp > 0 else 999
        if adequacy < 1.0:
            reprice_pct = min((1.0 / adequacy) - 1.0, 0.50)  # cap at +50%
            reprice_sar = ep * reprice_pct
            reprice_cr_pts = reprice_pct          # = reprice_sar / ep
        else:
            reprice_sar, reprice_cr_pts = 0.0, 0.0

        # ── LEVER 2: Bonus-Malus ─────────────────────────────────────
        # Source: claim frequency (CLAIM_COUNT / EXPOSURE_YEARS)
        # High-frequency segments have more repeat claimants to surcharge
        freq = cc / exp_yrs if exp_yrs > 0 else 0
        bm_pct = min(freq * 0.15, 0.05)          # 15% of freq, cap 5%
        bm_sar = ep * bm_pct
        bm_cr_pts = bm_pct

        # ── LEVER 3: UW Controls ─────────────────────────────────────
        # Source: average severity (ULTIMATE_CLAIMS / CLAIM_COUNT)
        # Preferred garage routing → 15% severity reduction, 50% realization
        avg_sev = uc / cc if cc > 0 else 0
        uw_sar = uc * 0.15 * 0.50
        uw_cr_pts = uw_sar / ep if ep > 0 else 0

        # ── LEVER 4: Claims Management ───────────────────────────────
        # Source: ULTIMATE_CLAIMS (5% recoverable via faster settlement)
        cm_sar = uc * 0.05
        cm_cr_pts = cm_sar / ep if ep > 0 else 0

        # ── LEVER 5: Distribution & Mix ──────────────────────────────
        # Source: SAFE_ZONE_STATUS — Loss segments shrink 20% exposure
        if seg['SAFE_ZONE_STATUS'] == 'Loss':
            mix_sar = gap_sar * 0.10              # 10% of gap from mix shift
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
            'gap_cr_pts': round(gap_cr, 4),
            'gap_sar': round(gap_sar, 2),
            'lever_repricing_cr_pts': round(reprice_cr_pts, 4),
            'lever_repricing_sar': round(reprice_sar, 2),
            'lever_bonusmalus_cr_pts': round(bm_cr_pts, 4),
            'lever_bonusmalus_sar': round(bm_sar, 2),
            'lever_uw_controls_cr_pts': round(uw_cr_pts, 4),
            'lever_uw_controls_sar': round(uw_sar, 2),
            'lever_claims_mgmt_cr_pts': round(cm_cr_pts, 4),
            'lever_claims_mgmt_sar': round(cm_sar, 2),
            'lever_distribution_cr_pts': round(mix_cr_pts, 4),
            'lever_distribution_sar': round(mix_sar, 2),
            'after_actions_cr': round(cr, 4),
            'after_actions_sar': 0,
            'baseline_cr': round(cr, 4),
            'baseline_earned_premium': round(ep, 2),
            'baseline_ultimate_claims': round(uc, 2),
            'baseline_claim_count': int(cc),
            'baseline_exposure_years': round(exp_yrs, 2),
            'baseline_avg_severity': round(avg_sev, 2),
            'baseline_frequency': round(freq, 4),
            'pricing_adequacy': round(adequacy, 4),
            'safe_zone_status': seg['SAFE_ZONE_STATUS'],
            'credibility_flag': seg['CREDIBILITY_FLAG'],
        })


    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("Loading fact_segment_performance...")
    seg_perf_df = pd.read_csv(INPUT_PATH)
    print(f"  Raw rows: {len(seg_perf_df)}")

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