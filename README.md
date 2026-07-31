# Moody's RDC Compliance Pipeline

Local pipeline for loading and querying the **Moody's GRID / RDC** compliance data feed (entities, relationships, sources) into DuckDB.

## Prerequisites

- Python 3.12+
- The RDC feed files (XML + XSD) — stored locally, **never committed**

## Setup

```powershell
# 1. Create and activate venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure paths
copy .env.example .env
# Edit .env: set RDC_DATA_DIR to the folder the feed files should live in

# 4. (optional) Fetch the live feed via SFTP instead of a manual copy
# Edit .env: set SFTP_HOST, SFTP_USERNAME, SFTP_PASSWORD, SFTP_REMOTE_DIR
python src/fetch_sftp.py --dry-run       # list remote files/sizes first
python src/fetch_sftp.py                 # download into RDC_DATA_DIR

# 5. Verify everything is in place
python src/check_setup.py
```

## Loading the data

```powershell
# Inspect the XML structure first (confirms element names against your XSD)
python src/inspect_xml.py --xsd          # summarise rdc_entities.xsd
python src/inspect_xml.py --n 3          # print 3 sample entity records

# Load entities (~98 MB, takes a few minutes)
python src/load_entities.py

# Load relationships and sources
python src/load_relationships.py

# Query the result
python src/query_examples.py
python src/query_examples.py --entity-id 1234567
python src/query_examples.py --name "Smith"
```

Output is written to `data/output/rdc.duckdb` (gitignored).

## Schema assumptions

The loader targets `rdc_entities.xsd` as shipped with the RDC Full File sample. Key mappings:

| XSD element / attribute | DuckDB column |
|---|---|
| `Entity[@id]` | `entity_id` |
| `Entity[@type]` | `entity_type` |
| `Entity[@active]` | `is_active` |
| `Names/Name[@nameType=Primary]/FullName` | `primary_name` |
| `Names/Name[@nameType=Alias]/FullName` | `aliases` (JSON array) |
| `Gender` | `gender` |
| `DateOfBirth/{Year,Month,Day}` | `dob_year/month/day` |
| `Nationalities/Nationality` | `nationalities` (JSON array) |
| `Countries/Country` | `countries` (JSON array) |
| `Categories/Category[@categoryId]` | `category_ids` / `category_names` |
| `Sources/Source[@sourceId]` | `source_ids` / `source_names` |
| `Identifiers/Identifier[@type,@number]` | `identifier_types` / `identifier_nums` |

If your feed uses different element names, run `python src/inspect_xml.py` to see the actual structure, then update `ENTITY_TAG` and `extract_entity()` in `src/load_entities.py`.

## Project structure

```
.
├── config.yaml          # batch sizes, table names, file names (committed)
├── .env.example         # path template — copy to .env and fill in
├── requirements.txt
└── src/
    ├── check_setup.py        # verify environment before loading
    ├── fetch_sftp.py         # pull the live feed from Moody's SFTP server
    ├── inspect_xml.py        # schema discovery tool
    ├── load_entities.py      # streaming XML → DuckDB loader (entities)
    ├── load_relationships.py # streaming XML → DuckDB loader (relationships)
    ├── query_examples.py     # example queries and name search
    └── mcp_server.py         # MCP server for Claude Desktop integration
```
