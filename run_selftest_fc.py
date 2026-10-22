"""Run Layer 2's self-test inside FreeCAD.

    freecadcmd run_selftest_fc.py

Exists because ``freecadcmd`` does not set ``__name__ == "__main__"``, so a
module's own ``if __name__`` guard never fires. The main pytest suite shells
out to THIS file - see tests/test_fc_adapter.py.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from bimsteelauto_fc.selftest import main   # noqa: E402

code = main()
print(f"SELFTEST EXIT {code}", flush=True)
sys.stdout.flush()
sys.exit(code)
