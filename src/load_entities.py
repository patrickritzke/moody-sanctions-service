"""
Streaming XML → DuckDB loader for Moody's RDC entities feed.

Schema derived from rdc_entities.xsd (verified). Key structure:

  <rdc_entities>
    <entity_updates>
      <person>
        <entity_id>…</entity_id>
        <source_item_id>…</source_item_id>
        <entity_name>…</entity_name>
        <systemId>…</systemId>
        <entityDate>…</entityDate>
        <aliases>
          <alias><id/><name/><type/></alias>…
        </aliases>
        <date_of_births>
          <date_of_birth><id/><year/><month/><day/><circa/></date_of_birth>…
        </date_of_births>
        <attributes>
          <attribute><type/><value/></attribute>…   ← gender, nationality, country, …
        </attributes>
        <events>
          <event>
            <category/><sub_category/><date/><end_date/><description/>
            <referenceSource>…</referenceSource>
          </event>…
        </events>
        <referenceSources>
          <referenceSource><source_item_id/></referenceSource>…
        </referenceSources>
        <identifications>
          <identification>
            <id/><type/><value/><location/><country/><issueDate/><expireDate/>
          </identification>…
        </identifications>
        <positions>
          <position><name/><fromDate/><toDate/></position>…
        </positions>
        <addresses>
          <address>
            <id/><raw_format/><address_line1/><address_line2/>
            <city/><province/><postal_code/><country/><type/>
          </address>…
        </addresses>
      </person>
      <organization>   ← same structure minus date_of_births and positions
        …
      </organization>
    </entity_updates>
    <entity_deletions>
      <entity_id>…</entity_id>…
    </entity_deletions>
  </rdc_entities>
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

# Both element names are top-level entity types in rdc_entities.xsd
ENTITY_TAGS = {"person", "organization"}

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    entity_id            VARCHAR PRIMARY KEY,
    entity_type          VARCHAR,        -- 'person' or 'organization'
    source_item_id       VARCHAR,
    entity_name          VARCHAR,        -- canonical/primary name
    system_id            VARCHAR,
    entity_date          VARCHAR,
    -- Aliases (aliases/alias[])
    alias_names          VARCHAR,        -- JSON ["AKA Name 1", ...]
    alias_types          VARCHAR,        -- JSON ["Also Known As", ...]
    -- Date of birth (persons only; first entry if multiple)
    dob_year             INTEGER,
    dob_month            INTEGER,
    dob_day              INTEGER,
    dob_circa            BOOLEAN,
    -- Pulled from attributes/attribute[] by type value
    gender               VARCHAR,
    nationalities        VARCHAR,        -- JSON ["US", "RU"]
    countries            VARCHAR,        -- JSON ["US"]
    -- Events = list memberships (events/event[])
    event_categories     VARCHAR,        -- JSON ["Sanctions", "PEP"]
    event_sub_categories VARCHAR,        -- JSON ["OFAC SDN", "EU List"]
    event_dates          VARCHAR,        -- JSON ["2020-01-15", ...]
    event_end_dates      VARCHAR,        -- JSON ["2023-06-01", ...]
    event_descriptions   VARCHAR,        -- JSON ["...", ...]
    -- Reference sources (referenceSources/referenceSource[])
    source_item_ids      VARCHAR,        -- JSON ["src1", "src2"]
    -- Identifications (identifications/identification[])
    id_types             VARCHAR,        -- JSON ["Passport", ...]
    id_values            VARCHAR,        -- JSON ["ABC123", ...]
    id_countries         VARCHAR,        -- JSON ["US", ...]
    -- Positions (persons only, positions/position[])
    position_names       VARCHAR,        -- JSON ["President", ...]
    position_from_dates  VARCHAR,        -- JSON ["2018-01-01", ...]
    position_to_dates    VARCHAR,        -- JSON ["2022-01-01", ...]
    -- Addresses (addresses/address[])
    address_countries    VARCHAR,        -- JSON ["US", "RU"]
    address_cities       VARCHAR         -- JSON ["Washington", ...]
)
"""

INSERT_SQL = f"""
INSERT OR REPLACE INTO {TABLE} VALUES (
    ?,?,?,?,?,?,  ?,?,  ?,?,?,?,  ?,?,?,
    ?,?,?,?,?,  ?,  ?,?,?,  ?,?,?,  ?,?
)
"""


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def find_child(elem, tag: str):
    for c in elem:
        if strip_ns(c.tag) == tag:
            return c
    return None


def child_text(elem, tag: str) -> str | None:
    c = find_child(elem, tag)
    return (c.text or "").strip() or None if c is not None else None


def iter_children(elem, tag: str):
    for c in elem:
        if strip_ns(c.tag) == tag:
            yield c


def safe_int(val: str | None) -> int | None:
    try:
        return int(val) if val else None
    except ValueError:
        return None


def jdump(lst: list) -> str | None:
    return json.dumps(lst) if lst else None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def extract_entity(elem, entity_type: str) -> tuple:
    entity_id      = child_text(elem, "entity_id")
    source_item_id = child_text(elem, "source_item_id")
    entity_name    = child_text(elem, "entity_name")
    system_id      = child_text(elem, "systemId")
    entity_date    = child_text(elem, "entityDate")

    # Aliases ----------------------------------------------------------------
    alias_names: list[str] = []
    alias_types: list[str] = []
    aliases_block = find_child(elem, "aliases")
    if aliases_block is not None:
        for alias in iter_children(aliases_block, "alias"):
            name  = child_text(alias, "name")
            atype = child_text(alias, "type")
            if name:
                alias_names.append(name)
                alias_types.append(atype or "")

    # Date of birth (persons only; take first entry) -------------------------
    dob_year = dob_month = dob_day = dob_circa = None
    dobs_block = find_child(elem, "date_of_births")
    if dobs_block is not None:
        first_dob = find_child(dobs_block, "date_of_birth")
        if first_dob is not None:
            dob_year  = safe_int(child_text(first_dob, "year"))
            dob_month = safe_int(child_text(first_dob, "month"))
            dob_day   = safe_int(child_text(first_dob, "day"))
            circa_raw = child_text(first_dob, "circa")
            dob_circa = circa_raw in ("true", "1") if circa_raw else None

    # Attributes: gender / nationality / country -----------------------------
    gender: str | None = None
    nationalities: list[str] = []
    countries: list[str] = []
    attrs_block = find_child(elem, "attributes")
    if attrs_block is not None:
        for attr in iter_children(attrs_block, "attribute"):
            atype = (child_text(attr, "type") or "").lower()
            value = child_text(attr, "value")
            if not value:
                continue
            if "gender" in atype or "sex" in atype:
                gender = value
            elif "national" in atype:
                nationalities.append(value)
            elif "country" in atype:
                countries.append(value)

    # Events (list memberships) ----------------------------------------------
    event_categories: list[str]     = []
    event_sub_categories: list[str] = []
    event_dates: list[str]          = []
    event_end_dates: list[str]      = []
    event_descriptions: list[str]   = []
    events_block = find_child(elem, "events")
    if events_block is not None:
        for event in iter_children(events_block, "event"):
            event_categories.append(child_text(event, "category") or "")
            event_sub_categories.append(child_text(event, "sub_category") or "")
            event_dates.append(child_text(event, "date") or "")
            event_end_dates.append(child_text(event, "end_date") or "")
            event_descriptions.append(child_text(event, "description") or "")

    # Reference sources ------------------------------------------------------
    source_item_ids: list[str] = []
    refs_block = find_child(elem, "referenceSources")
    if refs_block is not None:
        for ref in iter_children(refs_block, "referenceSource"):
            sid = child_text(ref, "source_item_id")
            if sid:
                source_item_ids.append(sid)

    # Identifications --------------------------------------------------------
    id_types: list[str]     = []
    id_values: list[str]    = []
    id_countries: list[str] = []
    ids_block = find_child(elem, "identifications")
    if ids_block is not None:
        for idf in iter_children(ids_block, "identification"):
            id_types.append(child_text(idf, "type") or "")
            id_values.append(child_text(idf, "value") or "")
            id_countries.append(child_text(idf, "country") or "")

    # Positions (persons only) -----------------------------------------------
    position_names: list[str]      = []
    position_from_dates: list[str] = []
    position_to_dates: list[str]   = []
    pos_block = find_child(elem, "positions")
    if pos_block is not None:
        for pos in iter_children(pos_block, "position"):
            pname = child_text(pos, "name")
            if pname:
                position_names.append(pname)
                position_from_dates.append(child_text(pos, "fromDate") or "")
                position_to_dates.append(child_text(pos, "toDate") or "")

    # Addresses --------------------------------------------------------------
    address_countries: list[str] = []
    address_cities: list[str]    = []
    addr_block = find_child(elem, "addresses")
    if addr_block is not None:
        for addr in iter_children(addr_block, "address"):
            c = child_text(addr, "country")
            ci = child_text(addr, "city")
            if c:
                address_countries.append(c)
            if ci:
                address_cities.append(ci)

    return (
        entity_id,
        entity_type,
        source_item_id,
        entity_name,
        system_id,
        entity_date,
        jdump(alias_names),
        jdump(alias_types),
        dob_year, dob_month, dob_day, dob_circa,
        gender,
        jdump(nationalities),
        jdump(countries),
        jdump(event_categories),
        jdump(event_sub_categories),
        jdump(event_dates),
        jdump(event_end_dates),
        jdump(event_descriptions),
        jdump(source_item_ids),
        jdump(id_types),
        jdump(id_values),
        jdump(id_countries),
        jdump(position_names),
        jdump(position_from_dates),
        jdump(position_to_dates),
        jdump(address_countries),
        jdump(address_cities),
    )


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------

def stream_entities(xml_path: Path):
    """Yield (entity_tuple) for every <person> and <organization> in the feed."""
    context = etree.iterparse(str(xml_path), events=("end",), recover=True)
    for _, elem in context:
        tag = strip_ns(elem.tag)
        if tag in ENTITY_TAGS:
            yield extract_entity(elem, tag)
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load(xml_path: Path, db_path: Path) -> int:
    print(f"Source : {xml_path}")
    print(f"DB     : {db_path}")
    print(f"Table  : {TABLE}")

    con = duckdb.connect(str(db_path))
    con.execute(CREATE_TABLE_SQL)

    batch: list[tuple] = []
    total = 0

    pbar = tqdm.tqdm(unit=" entities", desc="Loading", dynamic_ncols=True)

    for row in stream_entities(xml_path):
        batch.append(row)
        total += 1
        pbar.update(1)

        if len(batch) >= BATCH_SIZE:
            con.executemany(INSERT_SQL, batch)
            batch.clear()

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

    con = duckdb.connect(str(DB_PATH), read_only=True)
    count = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    print(f"\nSELECT COUNT(*) FROM {TABLE}  →  {count:,}")
    types = con.execute(f"""
        SELECT entity_type, COUNT(*) c FROM {TABLE}
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    print("\nEntity types:")
    for t, c in types:
        print(f"  {t or '(null)':<20} {c:>8,}")
    con.close()


if __name__ == "__main__":
    main()
