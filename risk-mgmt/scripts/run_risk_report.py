"""CLI entry point - see risk_mgmt/cli.py for the actual implementation.

Example:
    python scripts/run_risk_report.py --mode local --strategy MeanRev1=reports/meanrev --dry-run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from risk_mgmt.cli import main

if __name__ == "__main__":
    main()
