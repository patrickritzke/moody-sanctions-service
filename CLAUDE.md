# Moody's RDC Compliance Pipeline — Session Context

## What this project is

A local pipeline to load, query, and eventually screen parties against the
**Moody's GRID / RDC compliance data feed**. The feed is a set of XML files
containing sanctioned individuals, PEPs, adverse media subjects, and other
compliance-relevant entities.

Long-term goals:
1. Clean DuckDB load of the full feed ← **current focus**
2. Profile the data (what lists, how many entities, country/type breakdowns)
3. Screen Intapp party data against the feed
4. Incremental delta loads (the `_1` suffix on filenames suggests deltas exist)

## Data feed (NOT in repo — gitignored)

Location: `C:\Users\patrickr\OneDrive - Intapp\Documents\Moody's GRID\RDC Full File Sample\`

| File | Size | Purpose |
|---|---|---|
| `rdc_entities_1.xml` | 95.7 MB | Main entity records — persons and organizations |
| `rdc_relationships_1.xml` | 4.1 MB | Relationships between entities |
| `rdc_sources_1.xml` | 3.5 MB | Source citations |
| `rdc_dictionary.xml` | 18.3 KB | Code/category dictionary |
| `rdc_entities.xsd` | 10 KB | Schema for entities (verified — loader is built against this) |

## Verified XML schema (rdc_entities.xsd)

Root: `<rdc_entities>` → `<entity_updates>` → `<person>` | `<organization>`

### Fields on both person and organization

| XML path | DuckDB column | Notes |
|---|---|---|
| `entity_id` (child element) | `entity_id` | Primary key |
| `source_item_id` | `source_item_id` | |
| `entity_name` | `entity_name` | Canonical/primary name |
| `systemId` | `system_id` | |
| `entityDate` | `entity_date` | |
| `aliases/alias[]/name` | `alias_names` | JSON array |
| `aliases/alias[]/type` | `alias_types` | JSON array — "Also Known As", etc. |
| `attributes/attribute[]` type+value | `gender`, `nationalities`, `countries` | type value determines which column |
| `events/event[]/category` | `event_categories` | JSON array — "Sanctions", "PEP", etc. |
| `events/event[]/sub_category` | `event_sub_categories` | JSON array — "OFAC SDN", "EU List", etc. |
| `events/event[]/date` | `event_dates` | JSON array |
| `events/event[]/end_date` | `event_end_dates` | JSON array |
| `events/event[]/description` | `event_descriptions` | JSON array |
| `referenceSources/referenceSource[]/source_item_id` | `source_item_ids` | JSON array |
| `identifications/identification[]` type+value+country | `id_types`, `id_values`, `id_countries` | JSON arrays |
| `addresses/address[]` country+city | `address_countries`, `address_cities` | JSON arrays |

### Person-only fields

| XML path | DuckDB column |
|---|---|
| `date_of_births/date_of_birth[0]` year/month/day/circa | `dob_year`, `dob_month`, `dob_day`, `dob_circa` |
| `positions/position[]` name+fromDate+toDate | `position_names`, `position_from_dates`, `position_to_dates` |

The feed also contains `<entity_deletions>` (list of entity_id only) and
`<entity_moved_to_riskid>` — the loader currently ignores both.

## Repo structure

```
CLAUDE.md                ← this file
config.yaml              ← batch sizes, table names, filenames (committed)
.env.example             ← path template — user copies to .env
requirements.txt         ← python-dotenv, lxml, duckdb, pyyaml, tqdm
src/
  check_setup.py         ← verify .env paths and feed files before loading
  inspect_xml.py         ← schema discovery: prints XSD tree + samples live records
  load_entities.py       ← streaming XML → DuckDB loader (entities) ✅ done
  load_relationships.py  ← streaming XML → DuckDB loader (relationships) ✅ done
  query_examples.py      ← overview stats, entity lookup, name search
data/output/             ← gitignored; rdc.duckdb lands here after loading
```

## Current state

- [x] Project scaffolding (gitignore, .env, requirements, config)
- [x] `load_entities.py` — written and schema-verified against real XSD
- [x] `load_relationships.py` — written (schema assumptions, not yet verified)
- [x] `mcp_server.py` — MCP server for Claude Desktop, reads DuckDB via `RDC_DB_PATH`
- [x] `fetch_sftp.py` — written; pulls the live feed from Moody's SFTP server into
      `RDC_DATA_DIR` using `SFTP_HOST`/`SFTP_USERNAME`/`SFTP_PASSWORD`/`SFTP_REMOTE_DIR`
      in `.env` (credentials now available — not yet run against the real server)
- [ ] Full load not yet run — needs to be executed on the Windows machine
- [ ] `rdc_relationships.xsd` and `rdc_sources.xsd` not yet inspected
- [ ] `rdc_dictionary.xml` not yet read — worth doing to understand category codes
- [ ] No screening logic yet

## Dev/test workflow

Use `--limit N` flag on the loader (to be added) to create a small dev DB:
```powershell
python src/load_entities.py --limit 5000   # fast dev slice
python src/load_entities.py                # full load
```

Output DB path is controlled by `OUTPUT_DIR` in `.env` and `db_filename` in
`config.yaml` — point at different files for dev vs. full.

## Environment

- Python 3.12 on Windows
- Venv at `.venv\` — activate with `.\.venv\Scripts\Activate.ps1`
- Data folder has an apostrophe in the path (`Moody's GRID`) — fine for
  pathlib but quote it if ever passing through a shell command
