# Shim: the contracts now live in common/contracts.py (shared with server/).
# Keeps the existing `from contracts_local import ...` lines working.
# TODO(hack): import from common.contracts directly and delete this file.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.contracts import CallBrief, CallEvent  # noqa: E402,F401
