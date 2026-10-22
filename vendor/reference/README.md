# Reference implementations — for verification only

Nothing in `bimsteelauto` imports anything in this folder at runtime. These
files exist so the verification suite can be **re-run on any machine, at any
time, without depending on a skill folder whose path carries a version hash
and moves when the skill is re-synced**.

| File | Used by | What it verifies |
|---|---|---|
| `aisc-steel-design/scripts/aisc360.py` | `tests/test_design_verification.py` | Cb (Eq. F1-1), Chapter D/E/F/G/H member strengths — a second, independently written AISC 360-16 implementation |
| `asce7-loads/scripts/combos.py` | `tests/test_loads_verification.py` | ASCE 7-16 Sect. 2.3.1 / 2.4.1 load combinations |
| `asce7-loads/scripts/gravity.py` | `tests/test_loads_verification.py` | Roof live load, Eq. 4.8-1 |
| `asce7-loads/scripts/asce7.py` | the two above | shared `Result` / `Check` types |

## Why this matters

A VERIFIED flag in this package means "an independent source reproduced this
number". If the independent source is no longer runnable, the flag is a claim
rather than a check. Keeping these files beside the tests turns the
verification from something that happened once into something anyone can
repeat.

Two findings came out of these comparisons and are recorded in the modules
themselves:

- **Shear φ.** The AISC reference hardcodes the rolled-shape φv = 1.00; a
  welded plate girder takes 0.90. `design/members.py` is the correct one and
  a test pins the 11.1% gap so it cannot quietly close.
- **Wind sign.** The ASCE reference fabricates a reversed-factor wind case,
  which is right for seismic and wrong for wind. `loads/combinations.py`
  handles direction as separate load cases instead — but it had the same
  defect first, and this comparison is what found it.

Do not edit these files. If a reference is wrong, record the difference in
the module that disagrees with it, as above.
