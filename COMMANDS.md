# repomap — Command Reference

> **Files:** `repomap.py` · `analyzers.py` · `llm.py` · `symbol_graph.py` · `report_server.py`  
> **Requires:** Python 3.10+, `git` on PATH, `click` (`pip install click`)

---

## Quick-start recipes

| Goal | Command |
|------|---------|
| Fastest possible scan | `python repomap.py <url>` |
| Save reports to a folder | `python repomap.py <url> -o ./reports` |
| Open interactive browser dashboard | `python repomap.py <url> --serve` |
| Full dashboard + symbol graph | `python repomap.py <url> --serve --symbols` |
| Add AI-written summary | `python repomap.py <url> --llm` |
| Everything at once | `python repomap.py <url> --serve --symbols --llm --llm-full-report -o ./reports` |
| Serve an existing report | `python report_server.py reports/repomap_<slug>.json` |
| Standalone symbol map only | `python symbol_graph.py ./local-clone ./output` |

---

## `repomap.py` — Full flag reference

```
python repomap.py <REPO_URL> [OPTIONS]
```

`REPO_URL` is any public `https://github.com/...` URL. A shallow clone is made automatically and deleted when done.

---

### Core output flags

| Flag | Default | What it does |
|------|---------|-------------|
| `-o`, `--output-dir PATH` | `.` (current dir) | Where to write the JSON and Markdown report files |
| `--keep-clone` | off | Keep the cloned repo on disk after the run (saved to `<output-dir>/repo_clone/`) |
| `--no-color` | off | Strip ANSI colour codes — useful when piping output to a log file or CI |
| `--json-only` | off | Skip the terminal summary entirely; only write the `.json` and `.md` files |

**Output files produced** (always, regardless of other flags):

| File | Contents |
|------|---------|
| `repomap_<slug>.json` | Full machine-readable report — every field, all analysis results |
| `repomap_<slug>.md` | Human-readable Markdown — paste into Notion, Confluence, or a PR |

**Examples:**
```bash
# Minimal — output to current directory
python repomap.py https://github.com/helloflask/flask-examples

# Save to a subfolder, no terminal noise
python repomap.py https://github.com/helloflask/flask-examples -o ./reports --json-only

# CI-friendly: no colour, files only
python repomap.py https://github.com/helloflask/flask-examples --no-color --json-only -o ./reports
```

---

### Deep analysis flags

Deep analysis runs automatically by default. It adds 9 analysis passes to the report:

| Pass name | What it produces |
|-----------|-----------------|
| `architecture` | Identifies architectural pattern (monolith, microservices, clean DDD, etc.) |
| `entry_point_confidence` | Scores every entry point 0–100 with signals explaining the score |
| `first_day_path` | Ordered onboarding checklist with status (found / missing / manual) |
| `naming_consistency` | Detects style clashes (snake_case vs camelCase, etc.) across files, dirs, functions |
| `route_detection` | Extracts every HTTP route with method, path, file, and framework |
| `parameter_tracking` | Inventories all env vars, config keys, and CLI flags; flags undocumented ones |
| `dependency_impact` | Cross-references declared deps against source files to show usage breadth |
| `hidden_complexity` | Scans for god files, dynamic dispatch, global state, hardcoded secrets, etc. |
| `flow_trace` | Traces import chains from entry points; surfaces most-imported modules |

| Flag | Default | What it does |
|------|---------|-------------|
| `--analyze` / `--no-analyze` | on | Run all deep analysis passes / skip them entirely |
| `--analyze-only NAME` | — | Run only the named pass. **Repeatable.** See pass names above. |

**Examples:**
```bash
# Default — all 9 passes run automatically
python repomap.py https://github.com/helloflask/flask-examples

# Skip deep analysis (faster, base scan only)
python repomap.py https://github.com/helloflask/flask-examples --no-analyze

# Run only architecture classification
python repomap.py https://github.com/helloflask/flask-examples --analyze-only architecture

# Run two specific passes
python repomap.py https://github.com/helloflask/flask-examples \
  --analyze-only hidden_complexity \
  --analyze-only route_detection

# All valid --analyze-only names:
#   first_day_path  naming_consistency  entry_point_confidence  flow_trace
#   parameter_tracking  route_detection  dependency_impact
#   hidden_complexity  architecture
```

---

### Symbol graph flags

Builds a cross-file symbol map: every function, class, variable, and type that is defined in one file and used in another. Writes two extra output files.

| Flag | Default | What it does |
|------|---------|-------------|
| `--symbols` | off | Enable the symbol graph analysis |
| `--symbols-min-files N` | `2` | Only include symbols used in N or more files (filters noise) |

**Extra output files produced when `--symbols` is used:**

| File | Contents |
|------|---------|
| `repomap_<slug>_symbols.json` | Full graph: every symbol, definition location, all consuming files, call sites |
| `repomap_<slug>_symbols.md` | Reference doc: overview table, top exporters, per-symbol sections with collapsible call-site tables |

**Examples:**
```bash
# Basic symbol map (symbols used in 2+ files)
python repomap.py https://github.com/cramessar/local-gpt --symbols

# Tighter filter — only symbols used in 3+ files
python repomap.py https://github.com/cramessar/local-gpt --symbols --symbols-min-files 3

# Save everything to a folder
python repomap.py https://github.com/cramessar/local-gpt --symbols -o ./reports
```

---

### Dashboard (browser) flags

Starts a local HTTP server and opens an interactive single-page dashboard in your browser. No internet connection required — everything is self-contained.

| Flag | Default | What it does |
|------|---------|-------------|
| `--serve` | off | Start the dashboard server after analysis finishes |
| `--port N` | `7878` | Port to serve on (auto-increments if taken) |
| `--no-open` | off | Start the server but don't auto-open the browser (useful for remote machines) |

**Dashboard sections:**
Summary · Friction · First Day Path · Architecture · Entry Points · Routes (searchable) · Parameters · Complexity · Dependencies (searchable) · Naming · Flow Trace · Symbol Graph (interactive force-directed canvas + searchable table)

> The Symbol Graph section is only populated if `--symbols` is also passed.

**Examples:**
```bash
# Open dashboard after scan
python repomap.py https://github.com/cramessar/local-gpt --serve

# Full dashboard with symbol graph
python repomap.py https://github.com/cramessar/local-gpt --serve --symbols

# Custom port
python repomap.py https://github.com/cramessar/local-gpt --serve --port 9090

# Server only, no auto-open (e.g. SSH tunnel scenario)
python repomap.py https://github.com/cramessar/local-gpt --serve --no-open --port 8080
```

---

### LLM flags

Calls an AI model to write a human-readable summary of the entry points and/or a full onboarding narrative. Optional — the tool works without any API key.

| Flag | Default | What it does |
|------|---------|-------------|
| `--llm` | off | Enable LLM analysis (auto-detects provider from env vars) |
| `--llm-provider NAME` | auto | `anthropic` · `openai` · `ollama` · `openai-compat` |
| `--llm-api-key KEY` | env | API key — falls back to `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. |
| `--llm-model NAME` | provider default | Model name override |
| `--llm-base-url URL` | provider default | Base URL for `openai-compat` or a custom Ollama host |
| `--llm-timeout N` | `60` | Request timeout in seconds |
| `--llm-full-report` | off | Also generate a 3-paragraph onboarding narrative (uses ~2× tokens) |

**Provider auto-detection order** (when `--llm` is used without `--llm-provider`):  
`ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `GROQ_API_KEY` → `TOGETHER_API_KEY` → `MISTRAL_API_KEY` → `FIREWORKS_API_KEY` → Ollama on localhost

**Supported providers and their defaults:**

| Provider flag | Env var | Default model |
|--------------|---------|---------------|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-20241022` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |
| `ollama` | none needed | `llama3` |
| `openai-compat` | depends on service | set via `--llm-model` |

**Examples:**
```bash
# Auto-detect from env
export ANTHROPIC_API_KEY=sk-ant-...
python repomap.py https://github.com/helloflask/flask-examples --llm

# Explicit Anthropic model
python repomap.py https://github.com/helloflask/flask-examples \
  --llm --llm-provider anthropic --llm-model claude-3-5-sonnet-20241022

# OpenAI
python repomap.py https://github.com/helloflask/flask-examples \
  --llm --llm-provider openai --llm-model gpt-4o

# Ollama (local, no key needed)
python repomap.py https://github.com/helloflask/flask-examples \
  --llm --llm-provider ollama --llm-model llama3

# Groq via openai-compat
export GROQ_API_KEY=gsk_...
python repomap.py https://github.com/helloflask/flask-examples \
  --llm --llm-provider openai-compat \
  --llm-base-url https://api.groq.com/openai \
  --llm-model llama3-70b-8192

# Entry point summary + full onboarding narrative
python repomap.py https://github.com/helloflask/flask-examples \
  --llm --llm-full-report
```

---

## `report_server.py` — Serve an existing report

Serves any previously generated JSON report as a dashboard without re-running the analysis.

```
python report_server.py <REPORT_JSON> [OPTIONS]
```

| Argument / Flag | What it does |
|-----------------|-------------|
| `REPORT_JSON` | Path to a `repomap_<slug>.json` file |
| `--symbols PATH` | Path to the corresponding `repomap_<slug>_symbols.json` (optional) |
| `--port N` | Port to serve on (default: `7878`) |
| `--no-open` | Don't auto-open the browser |

**Examples:**
```bash
# Serve a report you already generated
python report_server.py reports/repomap_helloflask_flask_examples.json

# With symbol graph
python report_server.py reports/repomap_helloflask_flask_examples.json \
  --symbols reports/repomap_helloflask_flask_examples_symbols.json

# Custom port, no auto-open
python report_server.py reports/repomap_helloflask_flask_examples.json \
  --port 9000 --no-open
```

---

## `symbol_graph.py` — Standalone symbol map

Run the cross-file symbol analysis against a **local repo clone** without going through `repomap.py`.

```
python symbol_graph.py <REPO_PATH> [OUTPUT_DIR]
```

| Argument | What it does |
|----------|-------------|
| `REPO_PATH` | Path to an already-cloned repository on disk |
| `OUTPUT_DIR` | Where to write `symbols.json` and `symbols.md` (defaults to current dir) |

**Examples:**
```bash
# Run against a local clone
python symbol_graph.py ./my-project

# Specify output directory
python symbol_graph.py ./my-project ./reports
```

---

## Common flag combinations

```bash
# ── Quickest scan, just the files ────────────────────────────────────────────
python repomap.py <url> -o ./reports

# ── Scan + open browser dashboard ────────────────────────────────────────────
python repomap.py <url> --serve

# ── Everything: dashboard + symbols + AI summary ─────────────────────────────
python repomap.py <url> --serve --symbols --llm --llm-full-report -o ./reports

# ── CI / automation (no colour, no browser, files only) ──────────────────────
python repomap.py <url> --no-color --json-only -o ./reports

# ── Fast scan, skip slow deep analysis ───────────────────────────────────────
python repomap.py <url> --no-analyze

# ── Just the architecture + hidden complexity passes ─────────────────────────
python repomap.py <url> \
  --analyze-only architecture \
  --analyze-only hidden_complexity

# ── Full analysis + save everything, keep the clone ──────────────────────────
python repomap.py <url> --symbols --keep-clone -o ./reports

# ── Serve a report you already generated, no re-scan ─────────────────────────
python report_server.py ./reports/repomap_<slug>.json \
  --symbols ./reports/repomap_<slug>_symbols.json

# ── Remote machine (start server, SSH tunnel to port 7878) ───────────────────
python repomap.py <url> --serve --no-open --port 7878
```

---

## What each output file contains

| File | When created | Contents |
|------|-------------|---------|
| `repomap_<slug>.json` | Always | Full report: scan metadata, all analysis results, friction scores, LLM output |
| `repomap_<slug>.md` | Always | Same data as a readable Markdown doc — good for sharing in PRs or wikis |
| `repomap_<slug>_symbols.json` | `--symbols` | Full symbol graph: all cross-file symbols, definitions, consumers, call sites |
| `repomap_<slug>_symbols.md` | `--symbols` | Readable symbol reference: top exporters, per-symbol sections with call-site tables |

> The `<slug>` is derived from the repo URL. `https://github.com/helloflask/flask-examples` → `repomap_helloflask_flask_examples`.

---

## Environment variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | `--llm` auto-detect | Anthropic API key |
| `OPENAI_API_KEY` | `--llm` auto-detect | OpenAI API key |
| `GROQ_API_KEY` | `--llm` auto-detect | Groq API key (openai-compat) |
| `TOGETHER_API_KEY` | `--llm` auto-detect | Together AI key (openai-compat) |
| `MISTRAL_API_KEY` | `--llm` auto-detect | Mistral API key (openai-compat) |
| `FIREWORKS_API_KEY` | `--llm` auto-detect | Fireworks AI key (openai-compat) |
| `OLLAMA_HOST` | `--llm` auto-detect | Custom Ollama host (default: `http://localhost:11434`) |
| `REPOMAP_LLM_API_KEY` | `--llm-api-key` | Override key for any provider via env instead of flag |
