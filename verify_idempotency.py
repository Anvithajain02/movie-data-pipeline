"""
verify_idempotency.py - proves that re-running etl.py changes nothing.

Usage (run `python etl.py` once first so there is data to compare):
    python verify_idempotency.py

What it does:
    1. snapshot every table: row count + an md5 fingerprint of all the row contents
    2. run etl.py again (with --max-calls 0, see below)
    3. snapshot again and compare

Why --max-calls 0: the API stage is meant to keep going with new movies on every
run (that's how 1000 calls get spread over a 9,700 movie dataset), so letting it
fetch new movies between the two snapshots would legitimately change the tables.
With 0 calls the CSV load is re-done in full and every table that already
holds API data is checked, which is what idempotency is about.
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

TABLES = ["movies", "users", "genres", "movie_genres", "ratings",
          "directors", "movie_directors", "movie_details", "omdb_fetch_log"]


def snapshot(engine):
    snap = {}
    with engine.connect() as conn:
        for table in TABLES:
            # md5 over the text form of every row, sorted, so row order doesn't matter
            count, fingerprint = conn.execute(text(
                f"SELECT COUNT(*), md5(COALESCE(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t"
            )).one()
            snap[table] = (count, fingerprint)
    return snap


def main():
    engine = create_engine(os.environ["DATABASE_URL"])
    etl_path = Path(__file__).resolve().parent / "etl.py"

    before = snapshot(engine)
    print("Re-running the pipeline...\n")
    result = subprocess.run([sys.executable, str(etl_path), "--max-calls", "0"])
    if result.returncode != 0:
        sys.exit("etl.py failed, cannot compare.")
    after = snapshot(engine)

    print("\n{:18s}{:>10s}{:>10s}   {}".format("table", "before", "after", "result"))
    all_same = True
    for table in TABLES:
        same = before[table] == after[table]
        all_same &= same
        print("{:18s}{:>10,}{:>10,}   {}".format(
            table, before[table][0], after[table][0], "same" if same else "CHANGED"))

    print("\nIDEMPOTENT: PASS" if all_same else "\nIDEMPOTENT: FAIL")
    sys.exit(0 if all_same else 1)


if __name__ == "__main__":
    main()