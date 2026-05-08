"""
Streaming XML → DuckDB loader for Moody's RDC relationships feed.

Expected structure (rdc_relationships.xsd):
  <Relationships>
    <Relationship id="…" type="…" active="0|1">
      <EntityId1>…</EntityId1>
      <EntityId2>…</EntityId2>
      <RelationshipType>…</RelationshipType>
      <Direction>…</Direction>       <!-- optional -->
      <StartDate>…</StartDate>       <!-- optional -->
      <EndDate>…</EndDate>           <!-- optional -->
      <Sources><Source sourceId="…"/></Sources>
    </Relationship>
  </Relationships>

Run inspect_xml.py --file relationships to verify before loading.
"""
import json
import os
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from lxml import etree
import tqdm
import yaml

load_dotenv()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

DATA_DIR   = Path(os.environ["RDC_DATA_DIR"])
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "data/output"))

XML_PATH   = DATA_DIR / config["files"]["relationships"]
DB_PATH    = OUTPUT_DIR / config["db_filename"]
TABLE      = config["tables"]["relationships"]
BATCH_SIZE = config["loader"]["batch_size"]

RELATIONSHIP_TAG = "Relationship"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    relationship_id   VARCHAR PRIMARY KEY,
    rel_type          VARCHAR,
    is_active         BOOLEAN,
    entity_id_1       VARCHAR,
    entity_id_2       VARCHAR,
    direction         VARCHAR,
    start_date        VARCHAR,
    end_date          VARCHAR,
    source_ids        VARCHAR
)
"""

INSERT_SQL = f"""
INSERT OR REPLACE INTO {TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def child_text(elem, tag: str) -> str | None:
    for c in elem:
        if strip_ns(c.tag) == tag:
            return (c.text or "").strip() or None
    return None


def extract_relationship(elem) -> tuple:
    rel_id    = elem.get("id") or elem.get("relationshipId") or elem.get("Id")
    rel_type  = elem.get("type") or elem.get("relType") or child_text(elem, "RelationshipType")
    active_raw = elem.get("active") or "1"
    is_active = active_raw not in ("0", "false", "False")

    entity_id_1 = child_text(elem, "EntityId1") or child_text(elem, "entity1Id")
    entity_id_2 = child_text(elem, "EntityId2") or child_text(elem, "entity2Id")
    direction   = child_text(elem, "Direction")
    start_date  = child_text(elem, "StartDate")
    end_date    = child_text(elem, "EndDate")

    source_ids: list[str] = []
    for c in elem:
        if strip_ns(c.tag) == "Sources":
            for src in c:
                if strip_ns(src.tag) == "Source":
                    sid = src.get("sourceId") or src.get("id") or (src.text or "").strip()
                    if sid:
                        source_ids.append(sid)

    return (
        rel_id,
        rel_type,
        is_active,
        entity_id_1,
        entity_id_2,
        direction,
        start_date,
        end_date,
        json.dumps(source_ids) if source_ids else None,
    )


def stream_relationships(xml_path: Path):
    context = etree.iterparse(str(xml_path), events=("end",), recover=True)
    for _, elem in context:
        if strip_ns(elem.tag) == RELATIONSHIP_TAG:
            yield extract_relationship(elem)
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]


def main():
    if not XML_PATH.exists():
        print(f"ERROR: {XML_PATH} not found.")
        sys.exit(1)

    print(f"Source : {XML_PATH}")
    print(f"DB     : {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))
    con.execute(CREATE_TABLE_SQL)

    batch, total = [], 0
    file_size = XML_PATH.stat().st_size
    pbar = tqdm.tqdm(total=file_size, unit="B", unit_scale=True, desc="Parsing")

    for row in stream_relationships(XML_PATH):
        batch.append(row)
        total += 1
        if len(batch) >= BATCH_SIZE:
            con.executemany(INSERT_SQL, batch)
            batch.clear()
            pbar.set_postfix(rows=total)

    if batch:
        con.executemany(INSERT_SQL, batch)

    pbar.close()
    con.close()
    print(f"\nDone — {total:,} relationships written to {DB_PATH}")


if __name__ == "__main__":
    main()
