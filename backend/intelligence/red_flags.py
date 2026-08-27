"""
backend/intelligence/red_flags.py
-----------------------------------
12 automated red-flag checks on extracted financial metrics.
Runs once per filing, async, non-blocking.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def check_red_flags(metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Runs 12 checks against a dict of extracted metrics.
    Returns a list of flagged items: [{check, severity, description}].

    metrics keys expected (all optional — skipped if missing):
        revenue_current, revenue_prior
        accounts_receivable_current, accounts_receivable_prior
        auditor_changed (bool)
        going_concern_language (bool)
        related_party_current, related_party_prior
        goodwill, total_assets
        operating_cash_flow, net_income
        revenue_recognition_changed (bool)
        segment_count_current, segment_count_prior
        non_gaap_disclosed (bool)
        risk_factor_count_current, risk_factor_count_prior
        inventory_current, inventory_prior, cogs_current, cogs_prior
        dso_current, dso_prior
    """
    flags = []

    def flag(check: str, severity: str, description: str):
        flags.append({"check": check, "severity": severity, "description": description})

    # 1. AR growing faster than revenue
    rev_c = metrics.get("revenue_current")
    rev_p = metrics.get("revenue_prior")
    ar_c = metrics.get("accounts_receivable_current")
    ar_p = metrics.get("accounts_receivable_prior")
    if all(v is not None and v != 0 for v in [rev_c, rev_p, ar_c, ar_p]):
        rev_growth = (rev_c - rev_p) / abs(rev_p)
        ar_growth = (ar_c - ar_p) / abs(ar_p)
        if ar_growth > rev_growth + 0.05:
            flag(
                "ar_growth_exceeds_revenue",
                "medium",
                f"AR grew {ar_growth:.1%} vs revenue {rev_growth:.1%} — potential collection issues.",
            )

    # 2. Auditor change
    if metrics.get("auditor_changed"):
        flag("auditor_changed", "high", "Auditor changed since prior filing.")

    # 3. Going-concern language
    if metrics.get("going_concern_language"):
        flag("going_concern", "critical", "Going-concern language detected in filing.")

    # 4. Related-party spikes
    rp_c = metrics.get("related_party_current")
    rp_p = metrics.get("related_party_prior")
    if rp_c is not None and rp_p is not None and rp_p != 0:
        if (rp_c - rp_p) / abs(rp_p) > 0.25:
            flag("related_party_spike", "medium", "Related-party transactions up >25% YoY.")

    # 5. Goodwill ratio
    goodwill = metrics.get("goodwill")
    total_assets = metrics.get("total_assets")
    if goodwill is not None and total_assets and total_assets != 0:
        ratio = goodwill / total_assets
        if ratio > 0.4:
            flag("high_goodwill_ratio", "medium", f"Goodwill is {ratio:.0%} of total assets.")

    # 6. OCF / net income sign mismatch
    ocf = metrics.get("operating_cash_flow")
    ni = metrics.get("net_income")
    if ocf is not None and ni is not None:
        if ni > 0 and ocf < 0:
            flag(
                "ocf_ni_sign_mismatch",
                "high",
                "Net income positive but operating cash flow negative.",
            )

    # 7. Revenue recognition change
    if metrics.get("revenue_recognition_changed"):
        flag("rev_rec_change", "medium", "Revenue recognition policy change disclosed.")

    # 8. Segment reporting change
    sc_c = metrics.get("segment_count_current")
    sc_p = metrics.get("segment_count_prior")
    if sc_c is not None and sc_p is not None and sc_c != sc_p:
        flag("segment_change", "low", f"Segment count changed: {sc_p} → {sc_c}.")

    # 9. Non-GAAP without GAAP comparison
    if metrics.get("non_gaap_disclosed") is False:
        flag("non_gaap_no_comparison", "low", "Non-GAAP metrics disclosed without clear GAAP reconciliation.")

    # 10. Risk factor growth
    rf_c = metrics.get("risk_factor_count_current")
    rf_p = metrics.get("risk_factor_count_prior")
    if rf_c is not None and rf_p is not None and rf_p != 0:
        if (rf_c - rf_p) / rf_p > 0.15:
            flag("risk_factor_growth", "low", f"Risk factors grew {(rf_c-rf_p)/rf_p:.0%} YoY.")

    # 11. Inventory / COGS mismatch
    inv_c = metrics.get("inventory_current")
    inv_p = metrics.get("inventory_prior")
    cogs_c = metrics.get("cogs_current")
    cogs_p = metrics.get("cogs_prior")
    if all(v is not None and v != 0 for v in [inv_c, inv_p, cogs_c, cogs_p]):
        inv_growth = (inv_c - inv_p) / abs(inv_p)
        cogs_growth = (cogs_c - cogs_p) / abs(cogs_p)
        if inv_growth > cogs_growth + 0.1:
            flag("inventory_cogs_mismatch", "medium", "Inventory growing faster than COGS.")

    # 12. DSO trend
    dso_c = metrics.get("dso_current")
    dso_p = metrics.get("dso_prior")
    if dso_c is not None and dso_p is not None:
        if dso_c > dso_p * 1.15:
            flag("dso_increase", "medium", f"DSO increased from {dso_p:.0f} to {dso_c:.0f} days.")

    return flags
