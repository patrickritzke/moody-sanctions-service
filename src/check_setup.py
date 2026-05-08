"""
Verify that .env paths resolve and all expected RDC feed files are present.
Run this before any loader: python src/check_setup.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import yaml

load_dotenv()

data_dir = Path(os.environ.get("RDC_DATA_DIR", ""))
output_dir = Path(os.environ.get("OUTPUT_DIR", "data/output"))

with open("config.yaml") as f:
    config = yaml.safe_load(f)

EXPECTED_FILES = [
    config["files"]["entities"],
    config["files"]["relationships"],
    config["files"]["sources"],
    config["files"]["dictionary"],
    config["files"]["entities_xsd"],
]

print("=== RDC Pipeline — Setup Check ===\n")

ok = True

if not data_dir or not data_dir.exists():
    print(f"  FAIL  RDC_DATA_DIR not set or does not exist: {data_dir!r}")
    ok = False
else:
    print(f"  OK    RDC_DATA_DIR = {data_dir}")

for fname in EXPECTED_FILES:
    fpath = data_dir / fname
    if fpath.exists():
        size_mb = fpath.stat().st_size / 1_048_576
        print(f"  OK    {fname} ({size_mb:.1f} MB)")
    else:
        print(f"  FAIL  {fname} not found at {fpath}")
        ok = False

output_dir.mkdir(parents=True, exist_ok=True)
print(f"  OK    OUTPUT_DIR = {output_dir} (exists or created)")

print()
if ok:
    print("All checks passed. Ready to load.")
else:
    print("One or more checks failed. Fix .env paths before running loaders.")
    sys.exit(1)
