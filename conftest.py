"""Make the vendored engineering libraries importable, for pytest and FreeCAD.

Why a vendor folder at all
--------------------------
``pyaisc360`` is on PyPI and installs normally.  The cold-formed engine
(``aisi_s100`` / ``direct_strength``) is not published anywhere - it ships
inside the steel-design-toolkit skill, at a path that carries a version
hash and changes whenever the skill is re-synced.  Importing it from there
would make the test suite depend on a path nobody controls.

So it is vendored here, and this file puts it on ``sys.path``.  The same
folder is what ``Init.py`` will add inside FreeCAD, so a purlin checked at
the command line and one checked in the GUI run identical code.

pytest imports ``conftest.py`` before collecting anything, which is early
enough for the module-level ``importorskip`` calls in the test files.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

#: Every folder that has to be importable.  Order matters only in that an
#: installed package of the same name would win over the vendored copy -
#: which is deliberate: if you pip-install a real one later, it takes over.
_PATHS = [
    _ROOT,                       # the peb folder itself
    _ROOT / "vendor",            # anything vendored as a package
    _ROOT / "vendor" / "cfs",    # aisi_s100 and direct_strength are flat
]

for _p in _PATHS:
    _s = str(_p)
    if _p.exists() and _s not in sys.path:
        sys.path.insert(0, _s)
