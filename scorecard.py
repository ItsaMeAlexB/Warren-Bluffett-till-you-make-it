#!/usr/bin/env python3
"""scorecard.py - S_unified shadow scorecard (fixed-threshold, F-score style).

S_i = sum_k theta_k(pi_i) * c_ik,   c_ik = +1 if component k passes its fixed
threshold for i, -1 if it fails, 0 if missing (excluded).

11 components, fixed thresholds, NO industry grouping, NO z-scores:
  ROA + , dROA + , CFO/Assets + , Accruals - , dLeverage - ,
  dLiquidity + , Share issuance - , dGrossMargin + , dAssetTurnover + ,
  R&D + (gated) , Capex + (gated)

Profitability state       pi = sigmoid(ROA * PI_SCALE) in (0,1)   (PI_SCALE fixed)
Overinvestment gate       g  = 1{cfo_assets>0} * (1 - sigmoid(dLeverage))
State-dependent weights:
  dLeverage : theta0 * (1 + lam*(1-pi))      (penalty rises when unprofitable)
  R&D, Capex: theta0 * g                     (reward only if internally funded)
  otherwise : theta0

theta0 and lam are PLACEHOLDERS (theta0=1.0, lam=0.5) until fit by shrinkage on
the calibration sample. SHADOW ONLY - never affects eligibility.
"""
import math

COMPONENTS = ["roa", "delta_roa", "cfo_assets", "accruals", "delta_leverage",
              "delta_liquidity", "share_issuance", "delta_gross_margin",
              "delta_aturn", "rd_intensity", "capex_intensity"]
GATED = {"rd_intensity", "capex_intensity"}
PI_SCALE = 10.0          # dummy k for pi=1/(1+exp(-k*ROA)); placeholder until fit

# Fixed "good" condition per component. Passing -> +1, failing -> -1, missing -> 0.
GOOD = {
    "roa": lambda x: x > 0.0,
    "delta_roa": lambda x: x > 0.0,
    "cfo_assets": lambda x: x > 0.0,
    "accruals": lambda x: x < 0.0,          # (NI-CFO)/Assets < 0 -> CFO>NI, quality
    "delta_leverage": lambda x: x < 0.0,    # leverage falling is good
    "delta_liquidity": lambda x: x > 0.0,   # current ratio rising
    "share_issuance": lambda x: x <= 0.0,   # no net share issuance
    "delta_gross_margin": lambda x: x > 0.0,
    "delta_aturn": lambda x: x > 0.0,
    "rd_intensity": lambda x: x > 0.0,
    "capex_intensity": lambda x: x > 0.0,
}


def _v(x):
    return x if isinstance(x, (int, float)) and x == x else None


def _sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-float(x)))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def s_unified(rows, theta0=None, lam=1.0):
    """Compute S_unified + pi + gate for each row (shadow; returns dicts)."""
    theta0 = theta0 or {k: 1.0 for k in COMPONENTS}
    out = []
    for r in rows:
        roa = _v(r.get("roa"))
        pi = _sigmoid(roa * PI_SCALE) if roa is not None else 0.0
        cfo = _v(r.get("cfo_assets"))
        cfo_pos = cfo is not None and cfo > 0
        dl = _v(r.get("delta_leverage"))
        dl_s = _sigmoid(dl) if dl is not None else 0.0
        g = (1.0 if cfo_pos else 0.0) * (1.0 - dl_s)
        total, contrib = 0.0, {}
        for k in COMPONENTS:
            x = _v(r.get(k))
            if x is None:
                contrib[k] = None
                continue
            val = 1.0 if GOOD[k](x) else -1.0
            th = theta0.get(k, 1.0)
            if k == "delta_leverage":
                th *= (1.0 + lam * (1.0 - pi))
            elif k in GATED:
                th *= g
            c = th * val
            contrib[k] = c
            total += c
        out.append({"S_unified": total, "pi": pi, "gate": g, "contrib": contrib})
    return out


def _check():
    import math
    good = {"roa": 0.2, "delta_roa": 0.05, "cfo_assets": 0.3, "accruals": -0.02,
            "delta_leverage": -0.05, "delta_liquidity": 0.1, "share_issuance": -0.01,
            "delta_gross_margin": 0.03, "delta_aturn": 0.1,
            "rd_intensity": 0.05, "capex_intensity": 0.04}
    bad = {"roa": -0.2, "delta_roa": -0.1, "cfo_assets": -0.1, "accruals": 0.2,
           "delta_leverage": 0.3, "delta_liquidity": -0.2, "share_issuance": 0.05,
           "delta_gross_margin": -0.05, "delta_aturn": -0.1,
           "rd_intensity": 0.05, "capex_intensity": 0.04}
    g, b = s_unified([dict(good), dict(bad)])
    assert g["S_unified"] > b["S_unified"], (g["S_unified"], b["S_unified"])
    assert g["pi"] > 0.5 > b["pi"], (g["pi"], b["pi"])
    assert b["gate"] == 0.0, b["gate"]              # CFO<0 -> gate off
    for k in GATED:
        assert b["contrib"][k] == 0.0, (k, b["contrib"][k])  # gated terms zeroed
    assert g["gate"] > 0.5, g["gate"]
    print("S_unified (fixed-threshold) self-check PASS")
    print(f"  good: S={g['S_unified']:+.2f} pi={g['pi']:.3f} gate={g['gate']:.3f}")
    print(f"  bad : S={b['S_unified']:+.2f} pi={b['pi']:.3f} gate={b['gate']:.3f}")


if __name__ == "__main__":
    _check()

