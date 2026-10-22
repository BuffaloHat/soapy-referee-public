# SREF — Infra, Ports & Config

> Source of truth: `infra/docker-compose.yml`, root `.env`, and `infra/.env`.
> Never hardcode ports, URLs or keys in code. This doc names keys; it never
> holds their values.

---

## Services & Ports

| Service | Purpose | Runs where | Host port(s) | Container name |
|---|---|---|---|---|
| **Ollama** | Local models — agent (and judge, Phase 6) | Host, outside Docker | **11434** | — |
| **Arize Phoenix** | Traces — UI and OTLP collector | Docker (`infra/`) | **6006** UI + OTLP HTTP · **4317** OTLP gRPC | `sref_phoenix` |
| **Langfuse web** | Traces — UI and OTLP endpoint | Docker (`infra/`) | **3100** → 3000 | `sref_langfuse_web` |
| **Langfuse worker** | Background ingestion | Docker (`infra/`) | none | `sref_langfuse_worker` |
| Langfuse Postgres | Users, projects, keys | Docker (`infra/`) | none (internal) | `sref_langfuse_postgres` |
| Langfuse ClickHouse | Trace storage | Docker (`infra/`) | none (internal) | `sref_clickhouse` |
| Langfuse Redis | Queue | Docker (`infra/`) | none (internal) | `sref_redis` |
| Langfuse MinIO | Event and media storage | Docker (`infra/`) | none (internal) | `sref_minio` |
| **LangSmith** | Traces — cloud, free tier | smith.langchain.com | — | — |

Docker ports are published on `127.0.0.1` only. The Langfuse databases have no
host port at all — they are reachable from inside the `infra_sref` network.

---

## URLs

| What | URL |
|---|---|
| Phoenix UI | http://localhost:6006 |
| Phoenix OTLP (traces) | http://localhost:6006/v1/traces |
| Langfuse UI | http://localhost:3100 |
| Langfuse OTLP (traces) | http://localhost:3100/api/public/otel/v1/traces |
| Langfuse read API (v4) | http://localhost:3100/api/public/v2/observations — the v1 `/traces` and `/sessions` endpoints are retired in v4 |
| LangSmith UI | https://smith.langchain.com → project `soapy-referee` |
| LangSmith OTLP (traces) | https://api.smith.langchain.com/otel/v1/traces |
| Ollama API | http://localhost:11434 |

---

## Credentials & Config Keys

Two `.env` files, read by two different programs:

- **Root `.env`** — read by **SREF** (the runner and tracing). How to reach
  each tool, and the client-side keys.
- **`infra/.env`** — read by **Docker Compose** when it starts the stack.
  Langfuse's internal passwords, its first-start login, and its project keys.

Both are gitignored. Templates: `.env.example` and `infra/.env.example`.

| Key | Lives in | Read by | What it is |
|---|---|---|---|
| `OLLAMA_HOST` | `.env` | SREF | Ollama address. Defaults to `http://localhost:11434` |
| `NBA_SQLITE_PATH` | `.env` | SREF | Snapshot path. Defaults to `data/nba.sqlite` |
| `SREF_ENV` | `.env` or the shell | SREF | Run label: `dev` (default) or `eval` for runs that count. Shown as the trace environment; written to the run record |
| `OPENAI_API_KEY` | `.env` | SREF | OpenAI key for the comparison model (Phase 7, `agent/openai_api.py`) |
| `OPENAI_BASE_URL` | `.env` (optional) | SREF | Defaults to `https://api.openai.com/v1` |
| `PHOENIX_COLLECTOR_ENDPOINT` | `.env` | SREF | Phoenix base URL. No key — Phoenix has no auth locally |
| `LANGFUSE_HOST` | `.env` | SREF | Langfuse base URL (`http://localhost:3100`) |
| `LANGFUSE_PUBLIC_KEY` | `.env` **and** `infra/.env` | SREF / Compose | Project key (`pk-lf-…`). **Must match in both files** |
| `LANGFUSE_SECRET_KEY` | `.env` **and** `infra/.env` | SREF / Compose | Project secret (`sk-lf-…`). **Must match in both files** |
| `LANGSMITH_API_KEY` | `.env` | SREF | LangSmith API key, from its Settings → API Keys |
| `LANGSMITH_PROJECT` | `.env` | SREF | `soapy-referee`. Also the Phoenix project name |
| `LANGSMITH_ENDPOINT` | `.env` (optional) | SREF | Defaults to `https://api.smith.langchain.com` |
| `LANGFUSE_INIT_USER_EMAIL` | `infra/.env` | Compose | Langfuse login created on first start (`admin@sref.local`) |
| `LANGFUSE_INIT_USER_PASSWORD` | `infra/.env` | Compose | Password for that login |
| `POSTGRES_PASSWORD` | `infra/.env` | Compose | Langfuse Postgres |
| `CLICKHOUSE_PASSWORD` | `infra/.env` | Compose | Langfuse ClickHouse |
| `REDIS_AUTH` | `infra/.env` | Compose | Langfuse Redis |
| `MINIO_ROOT_PASSWORD` | `infra/.env` | Compose | Langfuse MinIO |
| `SALT` · `NEXTAUTH_SECRET` · `ENCRYPTION_KEY` | `infra/.env` | Compose | Langfuse internals. `ENCRYPTION_KEY` must be 64 hex chars (`openssl rand -hex 32`) |

**Logins**

| Tool | Login |
|---|---|
| Phoenix | None |
| Langfuse | `admin@sref.local` + `LANGFUSE_INIT_USER_PASSWORD`. Another account sees the project only once added to the `SREF` org (Organization settings → Members) |
| LangSmith | An existing LangSmith account (free tier) |

**Changing the Langfuse keys** after first start: the `infra/.env` values only
seed the project once. Create new keys in the Langfuse UI (Project settings →
API Keys), then update both files.

---

## Ollama Models

| Role | Model | Set in |
|---|---|---|
| Agent | `qwen2.5-coder:14b` | run config (`evals/configs/*.yaml`) |
| Judge (Phase 6) | `gemma4:12b` | judge config (`evals/configs/judge/`) |

The comparison model, `gpt-6.1-sol`, is not an Ollama model: it runs on
OpenAI, set in `evals/configs/openai-docs-r3.yaml` with `provider: openai`.
Models are run variables, so they live in configs, not `.env`.

---

## Start / Stop

```bash
cd infra
docker compose up -d        # start (first pull takes a few minutes)
docker compose ps           # status
docker compose stop         # stop, keep data
docker compose start        # start again
docker compose down         # remove containers, keep data (volumes)
docker compose down -v      # remove containers AND all trace data
```

Ollama runs on the host: `ollama serve` (or the menu-bar app).

The Langfuse stack uses about 4 GB of memory at idle, and ClickHouse keeps
using CPU with no traffic while its own system logs grow (6.8 GB here). Stop
the stack when not in use. Measurements: [observability_tools.md](observability_tools.md)
§5, *What it takes to run them*.

---

## Health Checks

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:6006/   # Phoenix -> 200
curl -s http://localhost:3100/api/public/health                   # Langfuse -> {"status":"OK",...}
curl -s http://localhost:11434/api/tags                           # Ollama -> pulled models
```

LangSmith has no local service — check the `soapy-referee` project in its UI.

---

## Tracing

The runner traces to every backend whose settings are present in `.env`, and
says which at startup (`tracing to: langfuse, langsmith, phoenix`). A backend
with missing settings is skipped, not an error. `--no-trace` turns tracing off
for one run. Details in [code_inventory.md](code_inventory.md) under
`tracing.py`.
