# Master Data Discovery Agent (L3)

An agentic system that retrieves and consolidates product master data from the
`enterprise_data` schema (`products`, `suppliers`) given **any** identifier —
product ID, part number, product name, supplier ID, or supplier name — without
any hardcoded identifier-detection or workflow logic.

## Architecture: MCP-based data access

The app owns **no SQL layer**. It is an MCP *client*: all database access goes
through an externally run **Postgres MCP server**, whose tools are discovered
at startup and exposed directly to the LLM. The agent writes read-only SQL
itself; the MCP server executes it against the database.

```
user query ──► [ agent loop ]                      (app/agent/agent.py)
                  │  reason: LLM sees the query, the discovered MCP tools,
                  │          the introspected schema summary, and all
                  │          observations so far, then decides the next action
                  │  act:    the chosen MCP tool runs (e.g. execute_sql)
                  │  observe: the raw rows are appended to the conversation
                  ▼  repeat until the agent itself decides the goal is met
              final answer + consolidated records + full reasoning trace

 app  ──(MCP client, app/mcp_client.py)──►  Postgres MCP server  ──►  PostgreSQL
```

- **The agent decides everything**: how to interpret the identifier, what SQL
  to run, when to retry with another interpretation, and when to stop. The
  prompt teaches it to collapse ambiguity into one query (a bare number is
  checked as product id, part number, AND supplier id at once with `OR`).
- **Tools come from the MCP server** (`app/agent/tools.py` is a passthrough):
  the server's own tools (`execute_sql`, `list_schemas`, `list_objects`,
  `get_object_details`, ...) are converted to function-calling specs.
  `MCP_TOOL_ALLOWLIST` keeps irrelevant server tools (DBA analysis) out of the
  prompt.
- **Schema is introspected, not hardcoded**: at startup one
  `information_schema` query (through the MCP server) builds a compact
  table/column/foreign-key summary that is injected into the system prompt, so
  the agent can write correct joins in turn 1 — with 2 tables today or 20
  later. Disable with `MCP_SCHEMA_IN_PROMPT=false` to force pure discovery via
  the server's schema tools.
- **Read-only, three times** (see "Read-only enforcement" below): a client
  guard, the server's restricted mode, and — the actual guarantee — a
  SELECT-only database role.
- **The harness never decides**: it executes the agent's tool calls, records
  observations, buckets retrieved rows (products / suppliers / other records),
  and enforces hard safety bounds — a step budget (`AGENT_MAX_STEPS`) and a
  wall-clock deadline (`AGENT_DEADLINE_SECONDS`).
- **Transparent execution**: every reasoning step, generated SQL statement,
  tool call, latency, and event is streamed live over SSE, returned in the
  response, logged as structured JSON, and persisted to
  `executions/<execution_id>.json`.

## Project layout

```
app/
  agent/
    agent.py        the ReAct loop (the agent) + SQL-generation prompt
    tools.py        MCP tool passthrough + read-only SQL guard
    audit.py        execution-trace persistence (executions/*.json)
  api/routes.py     /agent/query, /agent/query/stream, /tools, /agent/history
  llm.py            LLM client (Ollama native tools / OpenAI-compatible)
  mcp_client.py     sync MCP client (stdio/http/sse) + schema introspection
  database/         SQLAlchemy engine — used ONLY by scripts/init_db.py (seeding)
  schemas.py        Pydantic request/response models
ui/streamlit_app.py live agent UI (streams execution events)
scripts/init_db.sql schema + seed data
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Database

Start PostgreSQL with Docker (creates `master_data` with schema and seed
data automatically):

```powershell
docker compose up -d
```

Or initialize an existing PostgreSQL instance:

```powershell
python scripts/init_db.py
```

(`app/database/` and `scripts/init_db.py` exist only for this seeding step —
the running app never talks to the database directly.)

### MCP server

The agent needs a Postgres MCP server. The default configuration spawns
[`postgres-mcp`](https://pypi.org/project/postgres-mcp/) (installed via
`requirements.txt`) over **stdio** automatically — no separate deployment
needed, just start the API. To use an already-running network server instead:

| Variable | Meaning | Default |
|---|---|---|
| `MCP_TRANSPORT` | `stdio`, `http` (streamable HTTP), or `sse` | `stdio` |
| `MCP_SERVER_COMMAND` / `MCP_SERVER_ARGS` | stdio launch command | `postgres-mcp` / `--access-mode=restricted` |
| `MCP_SERVER_URL` | endpoint for `http`/`sse` transports | `http://localhost:8001/sse` |
| `MCP_DATABASE_URI` | connection string handed to the server | built from `POSTGRES_*` |
| `MCP_TOOL_ALLOWLIST` | MCP tools shown to the LLM (empty = all) | `execute_sql,list_schemas,list_objects,get_object_details` |
| `MCP_SCHEMA_IN_PROMPT` | inject introspected schema summary into the prompt | `true` |
| `MCP_SCHEMA_NAME` | schema to introspect | `enterprise_data` |

Example of running the server standalone (matches `MCP_TRANSPORT=sse`):

```powershell
$env:DATABASE_URI = "postgresql://postgres:postgres@localhost:5432/master_data"
postgres-mcp --access-mode=restricted --transport=sse
```

Verify with `curl http://localhost:8123/mcp-health` once the API is running —
it reports the connected server's tools and the introspected schema summary.

### Read-only enforcement

The agent can never write to the database. Three independent layers, weakest
first:

```
LLM-generated SQL
   │ 1. client guard (app/agent/tools.py) — single statement, SELECT/WITH
   │    only, no data-modifying keywords anywhere (blocks writing CTEs)
   │ 2. postgres-mcp --access-mode=restricted — parsed SQL, read-only tx
   ▼ 3. Postgres role `mcp_readonly` — SELECT-only grants +
        default_transaction_read_only: writes fail at the database
        even if layers 1 and 2 are bypassed entirely
```

- The restriction is scoped to the **agent path only**: the MCP server is the
  only thing connecting as `mcp_readonly`, so the LLM can never write — while
  admins, ETL jobs, and psql/pgAdmin sessions using their own credentials
  read and write normally.
- `scripts/init_db.py` creates/refreshes the `mcp_readonly` role
  (idempotent). New tables are covered automatically via
  `ALTER DEFAULT PRIVILEGES`.
- The MCP server connects as this role (`MCP_DB_USER` / `MCP_DB_PASSWORD`,
  defaults `mcp_readonly`/`mcp_readonly` for local dev — override the
  password via env in production). The admin `POSTGRES_*` credentials are
  used only by the seeding script, never by the agent path.
- Keep `--access-mode=restricted` in `MCP_SERVER_ARGS`.

### LLM

Default is a local Ollama model with native tool-calling:

```powershell
ollama pull qwen2.5:3b-instruct
```

`.env` options:

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `ollama` (native `/api/chat`) or `openai` (any OpenAI-compatible endpoint) |
| `LLM_MODEL` | `qwen2.5:3b-instruct`; use a stronger model (e.g. `qwen3:8b`) if SQL generation proves unreliable |
| `LLM_BASE_URL` / `LLM_API_KEY` | endpoint + key for hosted models |
| `LLM_TIMEOUT` | per-call timeout in seconds (default 300 — CPU prefill of the tool schemas is slow) |
| `AGENT_MAX_STEPS` / `AGENT_DEADLINE_SECONDS` | hard safety bounds on the loop |

On a CPU-only machine a typical query takes 1–3 reasoning steps (~40–90 s
total). Point `LLM_PROVIDER=openai` at a hosted model for sub-second steps.

## Run

```powershell
uvicorn app.main:app --reload --port 8123  # API on :8123 (spawns the MCP server via stdio)
streamlit run ui/streamlit_app.py  # UI
```

### API

- `POST /agent/query` — `{"query": "find details for product id 3731599"}` →
  final answer, consolidated records, reasoning trace (with generated SQL),
  tool calls, events, timings
- `POST /agent/query/stream` — same, but execution events stream live (SSE)
- `GET /tools` — the MCP tool catalog the agent sees
- `GET /agent/history` / `GET /agent/execution/{id}` — audit trail
- `GET /health`, `GET /mcp-health`

Example queries to try: `3731599` (product id), `A18-4` (part number),
`Acetone` (product name), `557` (supplier id), `Merck` (supplier name), or
natural language like `find details for product id 3731599`.
