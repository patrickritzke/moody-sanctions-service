"""
Fetch the Moody's GRID / RDC feed files from Moody's SFTP server into RDC_DATA_DIR.

Run this before the loaders to pull the live feed instead of a manually
copied sample:

    python src/fetch_sftp.py            # fetch all files listed in config.yaml
    python src/fetch_sftp.py --dry-run   # list remote files/sizes only

Requires these vars in .env:
    SFTP_HOST, SFTP_USERNAME, SFTP_PASSWORD
    SFTP_PORT        (optional, default 22)
    SFTP_REMOTE_DIR  (remote directory containing the feed files)
"""
import os
import sys
from pathlib import Path

import paramiko
import yaml
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

DATA_DIR = Path(os.environ["RDC_DATA_DIR"])

SFTP_HOST       = os.environ["SFTP_HOST"]
SFTP_PORT       = int(os.environ.get("SFTP_PORT", 22))
SFTP_USERNAME   = os.environ["SFTP_USERNAME"]
SFTP_PASSWORD   = os.environ["SFTP_PASSWORD"]
SFTP_REMOTE_DIR = os.environ.get("SFTP_REMOTE_DIR", ".")

FEED_FILES = [
    config["files"]["entities"],
    config["files"]["relationships"],
    config["files"]["sources"],
    config["files"]["dictionary"],
    config["files"]["entities_xsd"],
]


def connect() -> paramiko.SFTPClient:
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.connect(username=SFTP_USERNAME, password=SFTP_PASSWORD)
    return paramiko.SFTPClient.from_transport(transport)


def fetch(dry_run: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sftp = connect()
    try:
        for fname in FEED_FILES:
            remote_path = f"{SFTP_REMOTE_DIR.rstrip('/')}/{fname}"
            try:
                remote_size = sftp.stat(remote_path).st_size
            except FileNotFoundError:
                print(f"  MISSING  {remote_path}")
                continue

            size_mb = remote_size / 1_048_576
            if dry_run:
                print(f"  {fname:<25} {size_mb:>8.1f} MB  (remote)")
                continue

            local_path = DATA_DIR / fname
            with tqdm(total=remote_size, unit="B", unit_scale=True, desc=fname) as bar:
                sftp.get(remote_path, str(local_path),
                          callback=lambda done, total: bar.update(done - bar.n))
    finally:
        sftp.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="List remote files and sizes without downloading")
    args = parser.parse_args()

    print(f"=== Fetching RDC feed from {SFTP_HOST}:{SFTP_PORT}{SFTP_REMOTE_DIR} ===\n")
    try:
        fetch(dry_run=args.dry_run)
    except paramiko.AuthenticationException:
        print("ERROR: SFTP authentication failed — check SFTP_USERNAME/SFTP_PASSWORD in .env")
        sys.exit(1)

    if not args.dry_run:
        print(f"\nDone — files written to {DATA_DIR}")
        print("Run `python src/check_setup.py` to verify, then the loaders.")


if __name__ == "__main__":
    main()
