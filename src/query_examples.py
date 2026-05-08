"""
Example DuckDB queries against the loaded RDC data.

Usage:
    python src/query_examples.py
    python src/query_examples.py --entity-id 1234567
    python src/query_examples.py --name "Smith"
"""
import argparse
import json
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv
import yaml

load_dotenv()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

DB_PATH = Path(os.environ.get("OUTPUT_DIR", "data/output")) / config["db_filename"]


def fmt_json(val) -> str:
    if val is None:
        return ""
    try:
        items = json.loads(val)
        return ", ".join(str(i) for i in items) if items else ""
    except Exception:
        return str(val)


def run_overview(con):
    print("=== Database Overview ===\n")

    tables = con.execute("SHOW TABLES").fetchall()
    for (tname,) in tables:
        count = con.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
        print(f"  {tname}: {count:,} rows")

    print("\n=== Entity Types ===\n")
    rows = con.execute("""
        SELECT entity_type, COUNT(*) AS n
        FROM entities
        GROUP BY 1
        ORDER BY 2 DESC
    """).fetchall()
    for entity_type, n in rows:
        print(f"  {entity_type or '(null)':<30} {n:>8,}")

    print("\n=== Top 10 Category Names ===\n")
    rows = con.execute("""
        SELECT UNNEST(json_extract_string(category_names, '$[*]')) AS cat,
               COUNT(*) AS n
        FROM entities
        WHERE category_names IS NOT NULL
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 10
    """).fetchall()
    for cat, n in rows:
        print(f"  {cat:<40} {n:>8,}")

    print("\n=== Sample Active Entities ===\n")
    rows = con.execute("""
        SELECT entity_id, entity_type, primary_name, countries, category_names
        FROM entities
        WHERE is_active = true
        LIMIT 10
    """).fetchall()
    for eid, etype, name, ctys, cats in rows:
        print(f"  [{eid}] {etype} | {name}")
        print(f"         countries={fmt_json(ctys)}  categories={fmt_json(cats)}")


def lookup_entity(con, entity_id: str):
    row = con.execute("""
        SELECT * FROM entities WHERE entity_id = ?
    """, [entity_id]).fetchone()

    if row is None:
        print(f"No entity found with id={entity_id!r}")
        return

    cols = [d[0] for d in con.description]
    print(f"\n=== Entity {entity_id} ===\n")
    for col, val in zip(cols, row):
        if val is None:
            continue
        display = fmt_json(val) if col in (
            "nationalities", "countries", "category_ids", "category_names",
            "source_ids", "source_names", "aliases", "identifier_types",
            "identifier_nums", "positions"
        ) else val
        print(f"  {col:<22} {display}")

    # Pull relationships if table exists
    try:
        rels = con.execute("""
            SELECT relationship_id, rel_type, entity_id_1, entity_id_2
            FROM relationships
            WHERE entity_id_1 = ? OR entity_id_2 = ?
            LIMIT 20
        """, [entity_id, entity_id]).fetchall()
        if rels:
            print(f"\n  Relationships ({len(rels)}):")
            for rid, rtype, e1, e2 in rels:
                other = e2 if e1 == entity_id else e1
                print(f"    [{rid}] {rtype} → entity {other}")
    except duckdb.CatalogException:
        pass  # relationships table not loaded yet


def search_by_name(con, name: str):
    print(f"\n=== Name search: {name!r} ===\n")
    rows = con.execute("""
        SELECT entity_id, entity_type, primary_name, is_active, category_names
        FROM entities
        WHERE primary_name ILIKE ?
           OR aliases ILIKE ?
        LIMIT 20
    """, [f"%{name}%", f"%{name}%"]).fetchall()

    if not rows:
        print("  No matches.")
        return

    for eid, etype, pname, active, cats in rows:
        status = "ACTIVE" if active else "inactive"
        print(f"  [{eid}] {etype} | {pname} | {status} | {fmt_json(cats)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity-id", help="Look up a specific entity by ID")
    parser.add_argument("--name", help="Search entities by name (case-insensitive)")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)

    if args.entity_id:
        lookup_entity(con, args.entity_id)
    elif args.name:
        search_by_name(con, args.name)
    else:
        run_overview(con)

    con.close()


if __name__ == "__main__":
    main()
