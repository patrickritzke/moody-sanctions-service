"""
Streaming XML → DuckDB loader for Moody's RDC entities feed.

Schema is derived from rdc_entities.xsd. Run src/inspect_xml.py first to
verify element names if your feed version differs from the assumptions below.

Expected XML structure (no namespace):
  <Entities>
    <Entity id="…" type="Person|Company|…" active="0|1">
      <Names>
        <Name nameType="Primary|Alias|…" language="…">
          <FullName>…</FullName>
          <GivenName>…</GivenName>          <!-- persons -->
          <FamilyName>…</FamilyName>        <!-- persons -->
        </Name>
      </Names>
      <Gender>Male|Female</Gender>
      <DateOfBirth><Year/><Month/><Day/></DateOfBirth>
      <DateOfDeath><Year/><Month/><Day/></DateOfDeath>
      <Deceased>0|1</Deceased>
      <Nationalities><Nationality>ISO2</Nationality>…</Nationalities>
      <Countries><Country>ISO2</Country>…</Countries>
      <Categories><Category categoryId="…">label</Category>…</Categories>
      <Sources><Source sourceId="…">label</Source>…</Sources>
      <Identifiers>
        <Identifier type="…" country="…" number="…"/>
      </Identifiers>
      <Addresses>
        <Address><Street/><City/><Country/><PostalCode/></Address>
      </Addresses>
      <Positions><Position>…</Position>…</Positions>
      <LastUpdated>YYYY-MM-DD</LastUpdated>
    </Entity>
  </Entities>

If your XSD uses a namespace, set NAMESPACE below. The loader strips it
automatically using strip_ns(), so element lookups work either way.
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
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

XML_PATH   = DATA_DIR / config["files"]["entities"]
DB_PATH    = OUTPUT_DIR / config["db_filename"]
TABLE      = config["tables"]["entities"]
BATCH_SIZE = config["loader"]["batch_size"]
LOG_EVERY  = config["loader"]["log_every"]

# ---------------------------------------------------------------------------
# Element name of the repeating record in rdc_entities.xsd.
# Run inspect_xml.py --xsd to confirm; adjust here if needed.
# ---------------------------------------------------------------------------
ENTITY_TAG = "Entity"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    entity_id        VARCHAR PRIMARY KEY,
    entity_type      VARCHAR,
    is_active        BOOLEAN,
    primary_name     VARCHAR,
    given_name       VARCHAR,
    family_name      VARCHAR,
    gender           VARCHAR,
    dob_year         INTEGER,
    dob_month        INTEGER,
    dob_day          INTEGER,
    dod_year         INTEGER,
    dod_month        INTEGER,
    dod_day          INTEGER,
    deceased         BOOLEAN,
    nationalities    VARCHAR,
    countries        VARCHAR,
    category_ids     VARCHAR,
    category_names   VARCHAR,
    source_ids       VARCHAR,
    source_names     VARCHAR,
    aliases          VARCHAR,
    identifier_types VARCHAR,
    identifier_nums  VARCHAR,
    positions        VARCHAR,
    last_updated     VARCHAR
)
"""

INSERT_SQL = f"""
INSERT OR REPLACE INTO {TABLE} VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


def strip_ns(tag: str) -> str:
    """Strip XML namespace prefix from a tag name."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def child_text(elem, tag: str) -> str | None:
    """Return .text of first matching direct child, or None."""
    child = elem.find(tag)
    if child is None:
        # Try without namespace in case elem's children carry one
        for c in elem:
            if strip_ns(c.tag) == tag:
                return (c.text or "").strip() or None
    return (child.text or "").strip() or None if child is not None else None


def iter_children(elem, tag: str):
    """Yield all direct children whose local name matches tag."""
    for c in elem:
        if strip_ns(c.tag) == tag:
            yield c


def find_child(elem, tag: str):
    """Return first direct child matching local tag, or None."""
    for c in elem:
        if strip_ns(c.tag) == tag:
            return c
    return None


def safe_int(val: str | None) -> int | None:
    try:
        return int(val) if val else None
    except ValueError:
        return None


def parse_date_block(elem, block_tag: str):
    """Extract year/month/day integers from a <DateOfBirth> style block."""
    block = find_child(elem, block_tag)
    if block is None:
        return None, None, None
    return (
        safe_int(child_text(block, "Year")),
        safe_int(child_text(block, "Month")),
        safe_int(child_text(block, "Day")),
    )


def extract_entity(elem) -> tuple:
    """
    Map a single <Entity> element to a flat tuple matching INSERT_SQL.

    Element names are resolved by local name (strip_ns), so the loader
    works whether or not the feed declares an XML namespace.
    """
    entity_id   = elem.get("id") or elem.get("entityId") or elem.get("Id")
    entity_type = elem.get("type") or elem.get("entityType") or elem.get("Type")
    active_raw  = elem.get("active") or elem.get("isActive") or "1"
    is_active   = active_raw not in ("0", "false", "False", "no")

    # --- Names ---------------------------------------------------------------
    primary_name = given_name = family_name = None
    aliases: list[str] = []

    names_block = find_child(elem, "Names")
    if names_block is not None:
        for name_el in iter_children(names_block, "Name"):
            name_type = (name_el.get("nameType") or name_el.get("type") or "").lower()
            full = child_text(name_el, "FullName") or child_text(name_el, "fullName")
            given = child_text(name_el, "GivenName") or child_text(name_el, "givenName")
            family = child_text(name_el, "FamilyName") or child_text(name_el, "familyName")

            if name_type in ("primary", "primaryname", ""):
                if primary_name is None:
                    primary_name = full or (f"{given} {family}".strip() if given or family else None)
                    given_name  = given
                    family_name = family
            else:
                alias = full or (f"{given} {family}".strip() if given or family else None)
                if alias:
                    aliases.append(alias)

    # --- Gender & vital dates ------------------------------------------------
    gender  = child_text(elem, "Gender") or child_text(elem, "gender")
    dob_y, dob_m, dob_d = parse_date_block(elem, "DateOfBirth")
    dod_y, dod_m, dod_d = parse_date_block(elem, "DateOfDeath")
    deceased_raw = child_text(elem, "Deceased") or child_text(elem, "deceased")
    deceased = deceased_raw in ("1", "true", "True", "yes") if deceased_raw else None

    # --- Geo -----------------------------------------------------------------
    nationalities: list[str] = []
    nat_block = find_child(elem, "Nationalities")
    if nat_block is not None:
        for n in iter_children(nat_block, "Nationality"):
            val = (n.text or "").strip()
            if val:
                nationalities.append(val)

    countries: list[str] = []
    cty_block = find_child(elem, "Countries")
    if cty_block is not None:
        for c in iter_children(cty_block, "Country"):
            val = (c.text or "").strip()
            if val:
                countries.append(val)

    # --- Categories ----------------------------------------------------------
    category_ids: list[str] = []
    category_names: list[str] = []
    cat_block = find_child(elem, "Categories")
    if cat_block is not None:
        for cat in iter_children(cat_block, "Category"):
            cid = cat.get("categoryId") or cat.get("id") or cat.get("Id") or ""
            cname = (cat.text or "").strip()
            if cid:
                category_ids.append(cid)
            if cname:
                category_names.append(cname)

    # --- Sources -------------------------------------------------------------
    source_ids: list[str] = []
    source_names: list[str] = []
    src_block = find_child(elem, "Sources")
    if src_block is not None:
        for src in iter_children(src_block, "Source"):
            sid = src.get("sourceId") or src.get("id") or src.get("Id") or ""
            sname = (src.text or "").strip()
            if sid:
                source_ids.append(sid)
            if sname:
                source_names.append(sname)

    # --- Identifiers ---------------------------------------------------------
    identifier_types: list[str] = []
    identifier_nums: list[str] = []
    id_block = find_child(elem, "Identifiers")
    if id_block is not None:
        for idf in iter_children(id_block, "Identifier"):
            itype = idf.get("type") or idf.get("Type") or ""
            inum = idf.get("number") or idf.get("Number") or (idf.text or "").strip()
            if itype or inum:
                identifier_types.append(itype)
                identifier_nums.append(inum)

    # --- Positions -----------------------------------------------------------
    positions: list[str] = []
    pos_block = find_child(elem, "Positions")
    if pos_block is not None:
        for pos in iter_children(pos_block, "Position"):
            val = (pos.text or "").strip()
            if val:
                positions.append(val)

    # --- Misc ----------------------------------------------------------------
    last_updated = child_text(elem, "LastUpdated") or child_text(elem, "lastUpdated")

    return (
        entity_id,
        entity_type,
        is_active,
        primary_name,
        given_name,
        family_name,
        gender,
        dob_y, dob_m, dob_d,
        dod_y, dod_m, dod_d,
        deceased,
        json.dumps(nationalities)    if nationalities    else None,
        json.dumps(countries)        if countries        else None,
        json.dumps(category_ids)     if category_ids     else None,
        json.dumps(category_names)   if category_names   else None,
        json.dumps(source_ids)       if source_ids       else None,
        json.dumps(source_names)     if source_names     else None,
        json.dumps(aliases)          if aliases          else None,
        json.dumps(identifier_types) if identifier_types else None,
        json.dumps(identifier_nums)  if identifier_nums  else None,
        json.dumps(positions)        if positions        else None,
        last_updated,
    )


def stream_entities(xml_path: Path):
    """Yield parsed entity tuples by streaming the XML without full DOM load."""
    context = etree.iterparse(
        str(xml_path),
        events=("end",),
        tag=f"*",          # match any tag; we filter below to handle namespaces
        recover=True,
    )
    for _, elem in context:
        if strip_ns(elem.tag) == ENTITY_TAG:
            yield extract_entity(elem)
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]


def load(xml_path: Path, db_path: Path) -> int:
    print(f"Source : {xml_path}")
    print(f"DB     : {db_path}")
    print(f"Table  : {TABLE}")

    con = duckdb.connect(str(db_path))
    con.execute(CREATE_TABLE_SQL)

    batch: list[tuple] = []
    total = 0

    file_size = xml_path.stat().st_size
    pbar = tqdm.tqdm(
        total=file_size,
        unit="B",
        unit_scale=True,
        desc="Parsing",
        dynamic_ncols=True,
    )

    last_pos = 0

    for row in stream_entities(xml_path):
        batch.append(row)
        total += 1

        if len(batch) >= BATCH_SIZE:
            con.executemany(INSERT_SQL, batch)
            batch.clear()

            # Approximate byte progress via file position isn't directly
            # available from iterparse; we update based on record count.
            pbar.set_postfix(rows=total)
            if total % LOG_EVERY == 0:
                print(f"  {total:,} entities loaded…")

    if batch:
        con.executemany(INSERT_SQL, batch)

    pbar.close()
    con.close()
    return total


def main():
    if not XML_PATH.exists():
        print(f"ERROR: {XML_PATH} not found. Run check_setup.py first.")
        sys.exit(1)

    total = load(XML_PATH, DB_PATH)
    print(f"\nDone — {total:,} entities written to {DB_PATH}")
    print(f"\nQuick query check:")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    row = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()
    print(f"  SELECT COUNT(*) FROM {TABLE}  →  {row[0]:,}")
    types = con.execute(
        f"SELECT entity_type, COUNT(*) c FROM {TABLE} GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
    ).fetchall()
    print(f"\n  Entity types (top 10):")
    for t, c in types:
        print(f"    {t or '(null)':<25} {c:>8,}")
    con.close()


if __name__ == "__main__":
    main()
