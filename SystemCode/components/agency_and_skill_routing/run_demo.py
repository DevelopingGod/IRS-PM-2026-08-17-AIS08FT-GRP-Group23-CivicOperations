"""One-command demonstration: ``python run_demo.py``.

Runs the bundled example CO1 messages through the router with the packaged
catalog and prints a summary table plus one full JSON result. Works from a
fresh checkout or extracted ZIP without installation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from civicops_agency.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["demo"] + sys.argv[1:]))
