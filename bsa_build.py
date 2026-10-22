"""
Entry point for the FreeCAD half of the chain.

    freecadcmd bsa_build.py <job folder> [--out DIR]

``freecadcmd`` takes a script path and will not accept ``-m``, and it does
not set ``__name__ == "__main__"`` either, so the call is made at module
level rather than under a guard.  The logic lives in ``bsa.build`` so that
both interpreters run the same code.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "vendor")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from bsa.build import main  # noqa: E402

_code = main()
print(f"BSA BUILD EXIT {_code}", flush=True)
sys.stdout.flush()
sys.exit(_code)
