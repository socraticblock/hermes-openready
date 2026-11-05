"""Test bootstrap.

The plugin directory is loaded by Hermes as a package, so its ``__init__.py`` uses
package-relative imports. Tests import the implementation package directly, which
needs the repository root on ``sys.path``; this conftest supplies exactly that and
nothing else.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
