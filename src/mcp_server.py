"""
MCP server — Moody's RDC compliance data.

Exposes three tools to Claude Desktop:
  search_entities  — name / alias search
  lookup_entity    — full record by entity ID
  get_overview     — database stats (counts, top lists, top countries)

Claude Desktop config entry (claude_desktop_config.json):
{
  "mcpServers": {
    "rdc-compliance": {
      "command": "C:\\\\Users\\\\patrickr\\\\OneDrive - Intapp\\\\CODE\\\\rdc-pipeline\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["C:\\\\Users\\\\patrickr\\\\OneDrive - Intapp\\\\CODE\\\\rdc-pipeline\\\\src\\\\mcp_server.py"]
    }
  }
}
"""
import asyncio
import json
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
import yaml

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config.yaml") as f:
    config = yaml.safe_load(f)

# RDC_DB_PATH env var overrides the default location — set this in Claude
# Desktop config to point at a shared/OneDrive copy of the database.
_db_path_override = os.environ.get("RDC_DB_PATH")
DB_PATH = Path(_db_path_override) if _db_path_override else \
          ROOT / "data" / "output" / config["db_filename"]
TABLE   = config["tables"]["entities"]

# ---------------------------------------------------------------------------
# Code → label lookups from rdc_dictionary.xml
# ---------------------------------------------------------------------------

EVENT_CATEGORY: dict[str, str] = {
    "ABU": "Abuse (Domestic, Elder, Child)",
    "ARS": "Arson", "AST": "Assault, Battery",
    "BIL": "Questionable Billing Practices", "BKY": "Bankruptcy",
    "BLK": "Firm Specific Black List", "BRB": "Bribery / Corruption",
    "BUR": "Burglary", "BUS": "Business Crimes (Antitrust, Price Fixing)",
    "CFT": "Counterfeiting, Forgery", "CND": "Financial Condition Risk",
    "CON": "Conspiracy", "CPR": "Copyright Infringement",
    "CYB": "Cyber Crime", "DEF": "Default Risk", "DEN": "Denied Entity",
    "DPP": "Data Privacy and Protection", "DPS": "Possession of Drugs",
    "DTF": "Drug Trafficking", "DUI": "DUI / DWI",
    "ENV": "Environmental Crimes", "FAR": "Foreign Agent Registration Act",
    "FOF": "Former OFAC", "FOR": "Forfeiture",
    "FOS": "Former Sanctions List", "FRD": "Fraud, Scams, Swindles",
    "FUG": "Fugitive", "GAM": "Illegal Gambling",
    "HCD": "Health Care Disciplines", "HTE": "Hate Groups / Hate Crimes",
    "HUM": "Human Rights, Genocide, War Crimes",
    "IFO": "Information Only", "IGN": "Illegal Weapons / Explosives",
    "IMP": "Identity Theft, Impersonation", "IPR": "Illegal Prostitution",
    "IRC": "Iran Connect", "KID": "Kidnapping",
    "LMD": "Legal Marijuana Dispensary", "LNS": "Loan Sharking",
    "MIS": "Misconduct", "MLA": "Money Laundering",
    "MOR": "Mortgage Related", "MSB": "Money Services Business",
    "MUR": "Murder / Manslaughter", "NSC": "Non-Specific Crime",
    "OBS": "Obscenity / Child Pornography",
    "ORG": "Organized Crime / Racketeering",
    "PEP": "Politically Exposed Person", "PER": "Performance Risk",
    "PLT": "Public Intoxication / Trespassing",
    "PRJ": "Perjury / Obstruction of Justice",
    "PSP": "Possession of Stolen Property", "REG": "Regulatory Action",
    "REO": "Restructuring / Divestiture Risk", "RES": "Real Estate Actions",
    "ROB": "Robbery", "SEC": "SEC Violations / Securities Fraud",
    "SEX": "Sex Offenses", "SMG": "Smuggling",
    "SNX": "Sanctions Connect", "SPY": "Espionage / Treason",
    "TAX": "Tax Offenses", "TER": "Terrorist Related",
    "TFT": "Theft / Embezzlement / Extortion",
    "TRF": "People / Organ Trafficking",
    "VCY": "Virtual Currency", "WLT": "Watch List",
}

EVENT_SUB_CATEGORY: dict[str, str] = {
    "ACC": "Accused", "ACQ": "Acquitted / Not Guilty",
    "ACT": "Disciplinary / Regulatory Action", "ADT": "Audit",
    "ALL": "Alleged", "APL": "Appeal", "ARB": "Arbitration",
    "ARN": "Arraigned", "ART": "Arrested", "ASC": "Associated With",
    "CEN": "Censured", "CHG": "Charged", "CMP": "Complaint Filed",
    "CNF": "Confession", "CSP": "Conspired", "CVT": "Convicted",
    "DEP": "Deported", "DMS": "Dismissed", "EXP": "Expelled",
    "FIL": "Fined (<$10k)", "FIM": "Fined (>$10k)",
    "GOV": "Government Official", "IND": "Indicted",
    "LIC": "Licensing Action", "LIN": "Lien", "PLE": "Plea",
    "PRB": "Under Probe", "RVK": "Revoked Registration",
    "SAN": "Sanctioned", "SET": "Settlement / Suit", "SEZ": "Seizure",
    "SJT": "Served Jail Time", "SPD": "Suspended", "SPT": "Suspected",
    "TRL": "Trial", "WTD": "Wanted",
}


def decode(codes_json: str | None, lookup: dict[str, str]) -> str:
    """Decode a JSON array of codes to deduplicated human-readable labels."""
    if not codes_json:
        return ""
    try:
        seen, out = set(), []
        for c in json.loads(codes_json):
            if c and c not in seen:
                out.append(lookup.get(c, c))
                seen.add(c)
        return ", ".join(out)
    except Exception:
        return str(codes_json)


server = Server("rdc-compliance")


def get_con():
    return duckdb.connect(str(DB_PATH), read_only=True)


def fmt_json(val, sep=", ") -> str:
    if not val:
        return ""
    try:
        items = [i for i in json.loads(val) if i]
        return sep.join(items)
    except Exception:
        return str(val)


def fmt_entity_summary(row: tuple, cols: list[str]) -> str:
    d = dict(zip(cols, row))
    lines = []

    name = d.get("entity_name") or "(no name)"
    eid  = d.get("entity_id", "")
    etype = (d.get("entity_type") or "").capitalize()
    dob_y = d.get("dob_year")
    dob_str = f", b.{dob_y}" if dob_y else ""
    lines.append(f"{name} [{eid}] — {etype}{dob_str}")

    cats    = decode(d.get("event_categories"), EVENT_CATEGORY)
    subcats = decode(d.get("event_sub_categories"), EVENT_SUB_CATEGORY)
    if subcats:
        lines.append(f"  Status    : {subcats}")
    if cats:
        lines.append(f"  Categories: {cats}")

    ctys = fmt_json(d.get("countries"))
    nats = fmt_json(d.get("nationalities"))
    geo  = " / ".join(filter(None, [nats or ctys]))
    if geo:
        lines.append(f"  Countries : {geo}")

    aliases = fmt_json(d.get("alias_names"))
    if aliases:
        lines.append(f"  Aliases   : {aliases}")

    return "\n".join(lines)


def fmt_entity_full(row: tuple, cols: list[str]) -> str:
    d = dict(zip(cols, row))
    lines = []

    name  = d.get("entity_name") or "(no name)"
    eid   = d.get("entity_id", "")
    etype = (d.get("entity_type") or "").capitalize()
    lines.append(f"=== {name} ===")
    lines.append(f"Entity ID  : {eid}")
    lines.append(f"Type       : {etype}")

    if d.get("source_item_id"):
        lines.append(f"Source ID  : {d['source_item_id']}")
    if d.get("entity_date"):
        lines.append(f"Record date: {d['entity_date']}")

    # DOB
    dob_parts = [str(d[k]) for k in ("dob_year", "dob_month", "dob_day") if d.get(k)]
    if dob_parts:
        circa = " (circa)" if d.get("dob_circa") else ""
        lines.append(f"DOB        : {'-'.join(dob_parts)}{circa}")

    if d.get("gender"):
        lines.append(f"Gender     : {d['gender']}")

    nats = fmt_json(d.get("nationalities"))
    if nats:
        lines.append(f"Nationality: {nats}")

    ctys = fmt_json(d.get("countries"))
    if ctys:
        lines.append(f"Countries  : {ctys}")

    # Compliance events
    cats    = d.get("event_categories")
    subcats = d.get("event_sub_categories")
    dates   = d.get("event_dates")
    ends    = d.get("event_end_dates")
    descs   = d.get("event_descriptions")

    if cats:
        try:
            cat_list  = json.loads(cats)
            sub_list  = json.loads(subcats) if subcats else [""] * len(cat_list)
            date_list = json.loads(dates)   if dates   else [""] * len(cat_list)
            end_list  = json.loads(ends)    if ends    else [""] * len(cat_list)
            desc_list = json.loads(descs)   if descs   else [""] * len(cat_list)
            lines.append(f"\nCompliance events ({len(cat_list)}):")
            for cat, sub, dt, end, desc in zip(cat_list, sub_list, date_list, end_list, desc_list):
                cat_label = EVENT_CATEGORY.get(cat, cat)
                sub_label = EVENT_SUB_CATEGORY.get(sub, sub) if sub else ""
                entry = f"  • {cat_label}" + (f" — {sub_label}" if sub_label else "")
                if dt:
                    entry += f"  [{dt}" + (f" – {end}" if end else "") + "]"
                lines.append(entry)
                if desc:
                    lines.append(f"    {desc[:200]}")
        except Exception:
            lines.append(f"Events: {decode(cats, EVENT_CATEGORY)}")

    aliases = fmt_json(d.get("alias_names"))
    if aliases:
        lines.append(f"\nAliases    : {aliases}")

    # Identifications
    id_types = d.get("id_types")
    id_vals  = d.get("id_values")
    id_ctys  = d.get("id_countries")
    if id_types:
        try:
            rows_id = zip(
                json.loads(id_types),
                json.loads(id_vals)  if id_vals  else [],
                json.loads(id_ctys)  if id_ctys  else [],
            )
            lines.append("\nIdentifications:")
            for itype, ival, icty in rows_id:
                parts = filter(None, [itype, ival, icty])
                lines.append(f"  • {' | '.join(parts)}")
        except Exception:
            pass

    # Positions
    pos_names = d.get("position_names")
    if pos_names:
        try:
            pnames  = json.loads(pos_names)
            pfroms  = json.loads(d["position_from_dates"]) if d.get("position_from_dates") else [""] * len(pnames)
            ptos    = json.loads(d["position_to_dates"])   if d.get("position_to_dates")   else [""] * len(pnames)
            lines.append("\nPositions:")
            for pn, pf, pt in zip(pnames, pfroms, ptos):
                tenure = " – ".join(filter(None, [pf, pt]))
                lines.append(f"  • {pn}" + (f"  [{tenure}]" if tenure else ""))
        except Exception:
            pass

    # Addresses
    addr_ctys = fmt_json(d.get("address_countries"))
    addr_cits = fmt_json(d.get("address_cities"))
    if addr_ctys or addr_cits:
        lines.append(f"\nAddresses  : {', '.join(filter(None, [addr_cits, addr_ctys]))}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_entities",
            description=(
                "Search the Moody's RDC compliance database by name or alias. "
                "Returns matching persons and organizations with their compliance "
                "list memberships (sanctions, PEP status, adverse media, etc.). "
                "Optionally filter to only entities that appear on specific source lists "
                "by passing source_ids. Use get_overview first to see available source IDs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name to search for (partial match, case-insensitive)"
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of source IDs to filter by. Only entities linked to at least one of these sources will be returned."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 20)",
                        "default": 20
                    }
                },
                "required": ["name"]
            }
        ),
        types.Tool(
            name="lookup_entity",
            description=(
                "Get the full compliance record for a specific entity by its RDC entity ID. "
                "Returns all fields including compliance events, aliases, identifications, "
                "positions, and addresses."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "The RDC entity ID to look up"
                    }
                },
                "required": ["entity_id"]
            }
        ),
        types.Tool(
            name="get_overview",
            description=(
                "Get summary statistics for the loaded RDC compliance database: "
                "total entity counts, breakdown by type, top compliance lists, "
                "and top countries."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="search_by_sources",
            description=(
                "Search entities by name and explode results by source ID — "
                "one row per (entity, source) match. If an entity appears on "
                "three of your target lists it comes back as three rows. "
                "source_ids is required; use get_overview to find valid values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name to search for (partial match, case-insensitive)"
                    },
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Source IDs to filter and explode on. Required."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return (default 50)",
                        "default": 50
                    }
                },
                "required": ["name", "source_ids"]
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    con = get_con()
    try:
        if name == "search_entities":
            return await _search_entities(con, arguments)
        elif name == "search_by_sources":
            return await _search_by_sources(con, arguments)
        elif name == "lookup_entity":
            return await _lookup_entity(con, arguments)
        elif name == "get_overview":
            return await _get_overview(con)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    finally:
        con.close()


async def _search_entities(con, args: dict) -> list[types.TextContent]:
    query = args["name"].strip()
    limit = int(args.get("limit", 20))
    source_ids: list[str] = args.get("source_ids") or []
    pattern = f"%{query}%"

    # Build optional source filter: entity must have at least one matching source_item_id
    if source_ids:
        placeholders = ", ".join("?" * len(source_ids))
        source_filter = f"""
            AND EXISTS (
                SELECT 1 FROM (
                    SELECT UNNEST(json_extract_string(source_item_ids, '$[*]')) AS sid
                )
                WHERE sid IN ({placeholders})
            )
        """
        params_search = [pattern, pattern] + source_ids + [limit]
        params_count  = [pattern, pattern] + source_ids
    else:
        source_filter = ""
        params_search = [pattern, pattern, limit]
        params_count  = [pattern, pattern]

    rows = con.execute(f"""
        SELECT entity_id, entity_type, entity_name, dob_year, gender,
               nationalities, countries, event_categories, event_sub_categories,
               alias_names
        FROM {TABLE}
        WHERE (entity_name ILIKE ? OR alias_names ILIKE ?)
        {source_filter}
        ORDER BY entity_name
        LIMIT ?
    """, params_search).fetchall()

    cols = [d[0] for d in con.description]

    if not rows:
        msg = f'No entities found matching "{query}" in the RDC database.'
        if source_ids:
            msg += f' (filtered to sources: {", ".join(source_ids)})'
        return [types.TextContent(type="text", text=msg)]

    total = con.execute(f"""
        SELECT COUNT(*) FROM {TABLE}
        WHERE (entity_name ILIKE ? OR alias_names ILIKE ?)
        {source_filter}
    """, params_count).fetchone()[0]

    filter_note = f" — filtered to sources: {', '.join(source_ids)}" if source_ids else ""
    lines = [f'Found {total} match{"es" if total != 1 else ""} for "{query}"{filter_note}'
             + (f" (showing {limit})" if total > limit else "") + ":\n"]

    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {fmt_entity_summary(row, cols)}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _lookup_entity(con, args: dict) -> list[types.TextContent]:
    entity_id = str(args["entity_id"]).strip()

    row = con.execute(f"SELECT * FROM {TABLE} WHERE entity_id = ?", [entity_id]).fetchone()
    cols = [d[0] for d in con.description]

    if row is None:
        return [types.TextContent(
            type="text",
            text=f"No entity found with ID {entity_id!r}."
        )]

    return [types.TextContent(type="text", text=fmt_entity_full(row, cols))]


async def _search_by_sources(con, args: dict) -> list[types.TextContent]:
    query = args["name"].strip()
    source_ids: list[str] = args["source_ids"]
    limit = int(args.get("limit", 50))
    pattern = f"%{query}%"

    if not source_ids:
        return [types.TextContent(type="text",
                text="source_ids is required for this tool.")]

    placeholders = ", ".join("?" * len(source_ids))

    rows = con.execute(f"""
        SELECT e.entity_id, e.entity_type, e.entity_name, e.dob_year,
               e.event_categories, e.event_sub_categories, sid AS matched_source_id
        FROM {TABLE} e,
             (SELECT UNNEST(json_extract_string(source_item_ids, '$[*]')) AS sid)
        WHERE (e.entity_name ILIKE ? OR e.alias_names ILIKE ?)
          AND sid IN ({placeholders})
        ORDER BY e.entity_name, sid
        LIMIT ?
    """, [pattern, pattern] + source_ids + [limit]).fetchall()

    if not rows:
        return [types.TextContent(type="text",
                text=f'No matches for "{query}" on sources: {", ".join(source_ids)}')]

    lines = [
        f'Found {len(rows)} hit{"s" if len(rows) != 1 else ""} for "{query}" '
        f'across sources [{", ".join(source_ids)}]'
        + (" (limit reached)" if len(rows) == limit else "") + ":\n"
    ]

    for eid, etype, name, dob_y, cats, subcats, src in rows:
        dob_str  = f", b.{dob_y}" if dob_y else ""
        list_str = decode(subcats, EVENT_SUB_CATEGORY) or decode(cats, EVENT_CATEGORY)
        lines.append(f"  [{src}]  {name} [{eid}] — {etype}{dob_str}")
        if list_str:
            lines.append(f"           Lists: {list_str}")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_overview(con) -> list[types.TextContent]:
    total = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]

    types_rows = con.execute(f"""
        SELECT entity_type, COUNT(*) n FROM {TABLE}
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()

    top_lists = con.execute(f"""
        SELECT sub, COUNT(*) n
        FROM (
            SELECT UNNEST(json_extract_string(event_sub_categories, '$[*]')) AS sub
            FROM {TABLE} WHERE event_sub_categories IS NOT NULL
        )
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """).fetchall()

    top_countries = con.execute(f"""
        SELECT cty, COUNT(*) n
        FROM (
            SELECT UNNEST(json_extract_string(address_countries, '$[*]')) AS cty
            FROM {TABLE} WHERE address_countries IS NOT NULL
        )
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """).fetchall()

    lines = [f"RDC Compliance Database — {total:,} total entities\n"]

    lines.append("By type:")
    for t, n in types_rows:
        lines.append(f"  {t or '(unknown)':<20} {n:>8,}")

    lines.append("\nTop compliance lists (sub-categories):")
    for sub, n in top_lists:
        if sub:
            lines.append(f"  {sub:<45} {n:>6,}")

    lines.append("\nTop countries:")
    for cty, n in top_countries:
        if cty:
            lines.append(f"  {cty:<10} {n:>6,}")

    return [types.TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
