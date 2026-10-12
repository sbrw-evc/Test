"""Build the current Grafana v2 dashboard and fixed alert examples."""
import runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'scripts/build_operational_review.py'),run_name='__main__')
runpy.run_path(str(ROOT/'scripts/build_review_alerts.py'),run_name='__main__')
