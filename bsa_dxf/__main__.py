"""
``python -m bsa_dxf`` - write a drawing set from a design result.

Exit codes follow the ``bsa`` CLI convention::

    0   sheets written and verified clean
    1   bad arguments or the result could not be read
    3   sheets were written but something is wrong with them
"""

from __future__ import annotations

import argparse
import json
import sys

from .writer import write_set


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m bsa_dxf",
        description="Write DXF drawing sheets from a BIMSteelAuto design result.",
    )
    ap.add_argument("result", help="path to design_result.json")
    ap.add_argument("-o", "--out", default="dxf", help="output directory")
    ap.add_argument("--sheet", default="A1",
                    choices=["A0", "A1", "A2", "A3", "A4"])
    ap.add_argument("--scale", type=float, default=None,
                    help="plot scale denominator; omitted means fit the sheet")
    ap.add_argument("--prefix", default="GA")
    args = ap.parse_args(argv)

    try:
        with open(args.result, encoding="utf-8") as fh:
            result = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {args.result}: {exc}", file=sys.stderr)
        return 1

    out = write_set(result, args.out, sheet=args.sheet,
                    scale=args.scale, prefix=args.prefix)

    for sheet in out.sheets:
        print(f"  {sheet.name:<22} {sheet.scale:<8} {sheet.path}")
    for w in out.warnings:
        print(f"  WARNING  {w}")
    for e in out.errors:
        print(f"  ERROR    {e}", file=sys.stderr)

    if out.errors:
        return 3
    print(f"{len(out.sheets)} sheet(s) written to {out.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
