"""``python -m bsa`` -> :func:`bsa.cli.main`."""

from __future__ import annotations

import os
import sys

# Running as ``python -m bsa`` from outside the package root leaves the
# root off sys.path, so ``import bsa`` works but ``import bimsteelauto``
# does not.  Fixed before anything else is imported.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bsa.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
