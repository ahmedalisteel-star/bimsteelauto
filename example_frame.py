"""
example_frame.py — every connection on one PEB frame, one calc sheet.

Shows the point of task #8: six different design modules, one result
contract, one renderer. Adding a connection type adds a section to the
sheet for free.
"""

import bimsteelauto_connections as bc

MARK_PREFIX = "F1"
FRACTION = bc.CAPACITY_FRACTION      # 0.50 — client spec
results = []

# ======================================================================
# 1. EAVE — 4E moment end plate  (the only VERIFIED path)
# ======================================================================
Mu = bc.design_force(analysis=250.0, member_capacity=520.0,
                     fraction=FRACTION, label="Mu", unit="kNm")
Vu = bc.design_force(analysis=45.0, member_capacity=380.0,
                     fraction=FRACTION, label="Vu", unit="kN")

geom = bc.EndPlateGeometry(bp=250.0, g=140.0, pfo=50.0, pfi=50.0,
                           h_rows=(720.0, 600.0), de=45.0)
ep = bc.design_endplate(Mu=Mu.value, geom=geom, plate_grade="S355JR",
                        config=bc.EndPlateConfig.FOUR_E, strict=True)

rows = bc.bolt_row_forces(Mu=Mu.value, rows_mm=[720.0, 600.0], n_per_row=2)
row = bc.evaluate_bolt_row(
    Tu=rows.T_per_bolt, Vu=Vu.value / 8.0,
    bolt_grade="10.9", d_bolt=24.0,
    plate_grade="S355JR", t_plate=ep.t_plate,
    b=50.0, a=45.0, p=90.0,
)
Ff = bc.flange_force_from_moment(Mu.value, 720.0, 16.0)
wf = bc.size_fillet_weld(demand=Ff, length=250.0, t_part1=ep.t_plate,
                         t_part2=16.0, base_grade="S355JR", theta=90.0)

results.append(bc.ConnectionResult(
    mark=f"{MARK_PREFIX}-EAVE", kind="eave end plate",
    limit_states=(
        bc.LimitState("end plate flexure (DG 4 Eq. 3.10)",
                      ep.Mn_design, Mu.value, unit="kNm"),
        *[bc.LimitState(n, c, row.Tu if "tension" in n else row.Vu)
          for n, c, _ in ((n, row.T_available if "tension" in n
                           else row.V_available, u)
                          for n, u in (("bolt tension + prying", 0),
                                       ("bolt shear", 0)))],
        bc.LimitState("flange weld (J2)", wf.capacity, Ff),
    ),
    forces=(Mu, Vu),
    selections={"configuration": ep.config.value,
                "end plate": f"PL {ep.t_plate:.0f} mm S355JR",
                "bolts": "8 × M24 10.9",
                "flange weld": f"{wf.leg:.0f} mm fillet, {wf.electrode}"},
    geometry={"Yp (mm)": ep.yield_line.Yp,
              "Tu per bolt (kN)": rows.T_per_bolt},
    warnings=ep.yield_line.warnings if hasattr(ep.yield_line, "warnings") else (),
    verified=ep.yield_line.verified,
    citation=ep.yield_line.clause,
))

# ======================================================================
# 2. COLUMN SIDE — J10
# ======================================================================
cs = bc.check_column_side(
    Mu=Mu.value, d_beam=720.0, tf_beam=16.0, col_grade="S355JR",
    d_col=500.0, tw_col=10.0, tf_col=16.0, k_col=32.0, h_col=430.0,
    lb=24.0, Pr_col=200.0, Py_col=3000.0, bcf=200.0,
)
results.append(bc.from_checks(
    mark=f"{MARK_PREFIX}-COL", kind="column at haunch",
    checks=cs.checks, demand=cs.Puf,
    selections={"remedy": cs.remedy},
    geometry={"flange force Puf (kN)": cs.Puf},
    verified=True, citation=cs.citation,
))

# ======================================================================
# 3. BASE PLATE — net uplift governs
# ======================================================================
bg = bc.BasePlateGeometry(N=600.0, B=450.0, d=500.0, bf=200.0, f=250.0)
bp = bc.design_base_plate(Pu=-150.0, Mu=80.0, Vu=60.0, geom=bg,
                          plate_grade="S355JR", fc_prime=30.0, strict=False)
results.append(bc.ConnectionResult(
    mark=f"{MARK_PREFIX}-BASE", kind="base plate",
    limit_states=(
        bc.LimitState("concrete bearing (J8)", bp.phiPp, max(bp.Pu, 0.0)),
        bc.LimitState("plate thickness (DG 1)",
                      bp.t_selected, bp.t_required, unit="mm"),
    ),
    selections={"case": bp.case.value,
                "base plate": f"PL {bp.t_selected:.0f} mm S355JR",
                "anchor tension": f"{bp.T_anchor:.1f} kN"},
    geometry={"e (mm)": bp.eccentricity if bp.eccentricity != float("inf") else -1,
              "Y bearing (mm)": bp.Y_bearing},
    warnings=bp.warnings, verified=False, citation=bp.citation,
))

# ======================================================================
# 4. ANCHORS — fed straight from the base plate
# ======================================================================
ag = bc.AnchorGroup(d=30.0, hef=450.0, n_x=2, n_y=2, s_x=350.0, s_y=350.0,
                    ca_x1=450.0, ca_x2=450.0, ca_y1=450.0, ca_y2=450.0,
                    supplementary_reinforcement=True)
an = bc.check_anchors(Nua=bp.T_anchor, Vua=60.0, group=ag,
                      grade="F1554-55", fc_prime=30.0, strict=False)
results.append(bc.ConnectionResult(
    mark=f"{MARK_PREFIX}-ANCH", kind="anchor group",
    limit_states=(
        *[bc.LimitState(n, c, an.Nua) for n, c, _ in an.tension],
        *[bc.LimitState(n, c, an.Vua) for n, c, _ in an.shear],
    ),
    selections={"anchors": f"4 × M30 F1554-55, hef = {ag.hef:.0f} mm",
                "interaction": f"{an.interaction:.3f}",
                "rule": an.interaction_rule},
    geometry={"ANc/ANco": an.ANc_over_ANco},
    warnings=an.warnings, verified=False, citation=an.citation,
))

# ======================================================================
# 5 & 6. SECONDARY
# ======================================================================
pc = bc.check_purlin_cleat(Vu=15.0, Nu=5.0, d_bolt=12.0, n_bolts=2,
                           t_cleat=6.0, t_purlin=2.5, pitch=60.0,
                           edge_end=30.0, purlin_grade="S355JR",
                           # required since 2026-08-31 (defect #12): the
                           # bolt group's offset from the line of action.
                           # 40 mm is typical for a purlin cleat leg.
                           eccentricity=40.0,
                           strict=False)
results.append(bc.from_checks(
    mark=f"{MARK_PREFIX}-PUR", kind="purlin cleat",
    checks=pc.checks, demand=pc.demand,
    selections={"bolts": "2 × M12 8.8", "cleat": "PL 6 mm S275JR"},
    warnings=pc.warnings, verified=False, citation=pc.citation,
))

gu = bc.check_gusset(Pu=400.0, action="compression", t_gusset=12.0,
                     l_connection=300.0, w_first_row=100.0,
                     b_available=600.0, l_buckling=150.0, n_bolts=4,
                     strict=False)
results.append(bc.from_checks(
    mark=f"{MARK_PREFIX}-GUS", kind="bracing gusset",
    checks=gu.checks, demand=gu.demand,
    selections={"gusset": "PL 12 mm S355JR", "bolts": "4 × M20"},
    geometry=gu.details, warnings=gu.warnings,
    verified=False, citation=gu.citation,
))

# ======================================================================
# OUTPUT
# ======================================================================
print(bc.frame_summary(results))
print()
print(f"unverified relations: {len(bc.unverified())}")
print(f"ready for issue     : {bc.ready_for_issue()}")

html = bc.render_html(
    results,
    project="PEB Portal Frame — Connection Design",
    job_no="DEMO-001", designer="Ahmed Ali", checker="—",
)
with open("connection_calc_sheet.html", "w") as fh:
    fh.write(html)
print("\nwrote connection_calc_sheet.html")
