"""Make the unit test suite independent of installed SIL Kit native bindings.

Only ``tests/`` uses this fake. The live integration example in ``examples/``
imports the real ``SilKit_py`` when explicitly enabled.
"""

from pathlib import Path
import sys

from fake_silkit import register


# Use the test double before any imports of silkit_runner during test collection.
register()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
