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

JSON_COLS = {
    "alias_names", "alias_types", "nationalities", "countries",
    "event_categories", "event_sub_categories", "event_dates",
    "event_end_dates", "event_descriptions", "source_item_ids",
    "id_types", "id_values", "id_countries",
    "position_names", "position_from_dates", "position_to_dates",
    "address_countries", "address_cities",
}


def fmt(col: str, val) -> str:
    if val is None:
        return ""
    if col in JSON_COLS:
        try:
            items = json.loads(val)
            return ", ".join(str(i) for i in items if i) if items else ""
        except Exception:
            pass
    return str(val)


def run_overview(con):
    print("=== Database Overview ===\n")
    tables = con.execute("SHOW TABLES").fetchall()
    for (tname,) in tables:
        count = con.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
        print(f"  {tname}: {count:,} rows")

    print("\n=== Entity Types ===")
    for t, n in con.execute("""
        SELECT entity_type, COUNT(*) n FROM entities GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        print(f"  {t or '(null)':<20} {n:>8,}")

    print("\n=== Top 15 Event Categories ===")
    for cat, n in con.execute("""
        SELECT UNNEST(json_extract_string(event_categories, '$[*]')) AS cat,
               COUNT(*) AS n
        FROM entities
        WHERE event_categories IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """).fetchall():
        print(f"  {cat:<40} {n:>8,}")

    print("\n=== Top 15 Event Sub-categories ===")
    for sub, n in con.execute("""
        SELECT UNNEST(json_extract_string(event_sub_categories, '$[*]')) AS sub,
               COUNT(*) AS n
        FROM entities
        WHERE event_sub_categories IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """).fetchall():
        print(f"  {sub:<50} {n:>8,}")

    print("\n=== Sample Persons ===")
    for eid, name, dob_y, gender, cats in con.execute("""
        SELECT entity_id, entity_name, dob_year, gender, event_categories
        FROM entities WHERE entity_type = 'person' LIMIT 8
    """).fetchall():
        print(f"  [{eid}] {name}  DOB:{dob_y or '?'}  {gender or ''}  {fmt('event_categories', cats)}")

    print("\n=== Sample Organizations ===")
    for eid, name, cats in con.execute("""
        SELECT entity_id, entity_name, event_categories
        FROM entities WHERE entity_type = 'organization' LIMIT 8
    """).fetchall():
        print(f"  [{eid}] {name}  {fmt('event_categories', cats)}")


def lookup_entity(con, entity_id: str):
    row = con.execute("SELECT * FROM entities WHERE entity_id = ?", [entity_id]).fetchone()
    if row is None:
        print(f"No entity found with id={entity_id!r}")
        return
    cols = [d[0] for d in con.description]
    print(f"\n=== Entity {entity_id} ===\n")
    for col, val in zip(cols, row):
        if val is None:
            continue
        display = fmt(col, val)
        if display:
            print(f"  {col:<25} {display}")

    # Relationships if loaded
    try:
        rels = con.execute("""
            SELECT relationship_id, rel_type, entity_id_1, entity_id_2
            FROM relationships WHERE entity_id_1 = ? OR entity_id_2 = ?
            LIMIT 20
        """, [entity_id, entity_id]).fetchall()
        if rels:
            print(f"\n  Relationships ({len(rels)}):")
            for rid, rtype, e1, e2 in rels:
                other = e2 if e1 == entity_id else e1
                print(f"    [{rid}] {rtype} → entity {other}")
    except duckdb.CatalogException:
        pass


def search_by_name(con, name: str):
    print(f"\n=== Name search: {name!r} ===\n")
    rows = con.execute("""
        SELECT entity_id, entity_type, entity_name, dob_year, event_categories
        FROM entities
        WHERE entity_name ILIKE ?
           OR alias_names ILIKE ?
        LIMIT 20
    """, [f"%{name}%", f"%{name}%"]).fetchall()
    if not rows:
        print("  No matches.")
        return
    for eid, etype, ename, dob_y, cats in rows:
        dob_str = f"b.{dob_y}" if dob_y else ""
        print(f"  [{eid}] {etype} | {ename} {dob_str} | {fmt('event_categories', cats)}")


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
