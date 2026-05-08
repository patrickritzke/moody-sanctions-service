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

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config.yaml") as f:
    config = yaml.safe_load(f)

_output_dir = os.environ.get("OUTPUT_DIR")
DB_PATH = (Path(_output_dir) if _output_dir else ROOT / "data" / "output") / config["db_filename"]
TABLE   = config["tables"]["entities"]

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

    cats    = fmt_json(d.get("event_categories"))
    subcats = fmt_json(d.get("event_sub_categories"))
    if subcats:
        lines.append(f"  Lists     : {subcats}")
    elif cats:
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
                entry = f"  • {cat}" + (f" > {sub}" if sub else "")
                if dt:
                    entry += f"  [{dt}" + (f" – {end}" if end else "") + "]"
                lines.append(entry)
                if desc:
                    lines.append(f"    {desc[:200]}")
        except Exception:
            lines.append(f"Events: {fmt_json(cats)}")

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
                "Use this to screen a party name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name to search for (partial match, case-insensitive)"
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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    con = get_con()
    try:
        if name == "search_entities":
            return await _search_entities(con, arguments)
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
    pattern = f"%{query}%"

    rows = con.execute(f"""
        SELECT entity_id, entity_type, entity_name, dob_year, gender,
               nationalities, countries, event_categories, event_sub_categories,
               alias_names
        FROM {TABLE}
        WHERE entity_name ILIKE ?
           OR alias_names ILIKE ?
        ORDER BY entity_name
        LIMIT ?
    """, [pattern, pattern, limit]).fetchall()

    cols = [d[0] for d in con.description]

    if not rows:
        return [types.TextContent(
            type="text",
            text=f'No entities found matching "{query}" in the RDC database.'
        )]

    total = con.execute(f"""
        SELECT COUNT(*) FROM {TABLE}
        WHERE entity_name ILIKE ? OR alias_names ILIKE ?
    """, [pattern, pattern]).fetchone()[0]

    lines = [f'Found {total} match{"es" if total != 1 else ""} for "{query}"'
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


async def _get_overview(con) -> list[types.TextContent]:
    total = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]

    types_rows = con.execute(f"""
        SELECT entity_type, COUNT(*) n FROM {TABLE}
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()

    top_lists = con.execute(f"""
        SELECT UNNEST(json_extract_string(event_sub_categories, '$[*]')) AS sub,
               COUNT(*) n
        FROM {TABLE} WHERE event_sub_categories IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
    """).fetchall()

    top_countries = con.execute(f"""
        SELECT UNNEST(json_extract_string(countries, '$[*]')) AS cty,
               COUNT(*) n
        FROM {TABLE} WHERE countries IS NOT NULL
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
