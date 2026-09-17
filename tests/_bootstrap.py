"""Put ``src/`` on ``sys.path`` so tests can import the package in place.

There is no installer on this machine, so tests import from the source tree
directly. Every test module imports this first.
"""

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
