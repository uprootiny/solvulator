# Solvulator Architecture

## Backbone

```
system.py (:9800)
│
├── SheetEngine          — CSV/Google Sheets → validated rows
│   ├── load(source)     — fetch + parse + validate
│   ├── view(density)    — summary/focused/neighborhood/full projections
│   └── snapshot()       — immutable full state
│
├── TokenEngine          — scoped auth tokens
│   ├── mint(scope)      — PILOT/EXPORT/DISPATCH/ADMIN
│   ├── check(token)     — validate + scope check
│   └── revoke(token)    — invalidate
│
├── LLM Proxy            — hides API keys from browser
│   ├── call_claude()    — Anthropic → Gemini → OpenRouter fallback chain
│   ├── call_gemini()    — direct Gemini API
│   └── call_lathe()     — LATHE argument decomposition pre-pass
│
├── PipelineEngine       — 12-agent orchestration
│   ├── start_run()      — begin document processing
│   ├── run_step()       — advance one agent (respects human gates)
│   ├── approve_gate()   — human approves gate to continue
│   └── get_run()        — full run state + artifacts
│
└── HTTP Server           — stdlib, no framework
    ├── GET  /health, /manifest, /catalog
    ├── GET  /sheet/state, /sheet/view, /sheet/snapshot
    ├── POST /sheet/reload
    ├── POST /token/mint, GET /token/check, POST /token/revoke
    ├── POST /agent/claude, /agent/gemini, /agent/lathe
    ├── POST /pipeline/start
    ├── GET  /pipeline/<run_id>
    ├── POST /pipeline/<run_id>/step
    ├── POST /pipeline/<run_id>/approve
    ├── GET  /pipeline/runs
    └── GET  / (serves static/index.html)
```

## 12-Agent Pipeline

```
PHASE: INTAKE
  01 Intake & Classification      [HUMAN GATE]  → source, type, urgency, deadlines
  02 OCR & Text Integrity         [HUMAN GATE]  → clean text, quality score

PHASE: ANALYSIS
  03 Operative Extraction          [HUMAN GATE]  → deadlines, filings, sanctions
  04 Legal Structure Mapper                      → claims, proportionality, precedents
  05 Weakness Detector                           → contradictions, gaps, exploitable flaws

PHASE: INTELLIGENCE
  06 FOI Trigger Analyzer                        → freedom of info request drafts
  07 Damage Exposure Calculator                  → ILS damage estimates by category

PHASE: ACTION
  08 Strategy Builder              [HUMAN GATE]  → ranked alternatives, risk/benefit
  09 Draft Generator               [HUMAN GATE]  → full Hebrew legal document draft

PHASE: CONTROL
  10 Human Verification Gate       [HUMAN GATE]  → 8-point pass/warn/fail checklist
  11 Filing & Dispatch Controller                → channels, recipients, steps
  12 Meta-Pattern Recorder                       → institutional patterns (no PII)
```

### Pipeline Invariants

1. No agent skipped (except 02 for born-digital docs)
2. No downstream agent consumes ungated data
3. No dispatch without agent 10 approval
4. No FOI auto-send — all require human approval
5. No aggressive language or personal attribution
6. No personal identifiers in pattern data
7. All estimates state assumptions and confidence
8. Dates: ISO 8601 (Hebrew calendar converted)
9. Amounts: ILS unless stated
10. Agent 09 style failures require revision
11. Agent 12 append-only
12. Every document passes all applicable stages

### Data Flow

Each agent receives:
- Original document text
- All prior agent outputs (cumulative JSON envelope)

Each agent returns:
- Structured JSON per its schema
- Saved as `artifacts/<run_id>/step_NN_<agent_id>.json`

Human gates pause the pipeline. The UI shows the gate, the user reviews and approves/rejects.

## LLM Provider Fallback Chain

```
1. Anthropic API  (ANTHROPIC_API_KEY, claude-sonnet-4)
2. Gemini API     (GEMINI_API_KEY, gemini-2.0-flash)
3. OpenRouter     (OPENROUTER_API_KEY, anthropic/claude-sonnet-4)
```

Keys loaded from: environment vars → ~/.env.openrouter → POST /api/key (runtime injection)

## Sheet Engine

Connects to Google Sheets (published CSV URL) or local CSV files.

Density views:
- `summary`: sv_id, status
- `focused`: + urgency, stage, source
- `neighborhood`: + document_type, amount
- `full`: all fields except _raw

Row schema: SV_ID, Stage, Status, Source, Document_Type, Urgency, Amount
Validation: REQUIRED_COLUMNS enforced, status ∈ {PENDING, RUNNING, DONE, BLOCKED}

## Token Auth

Scopes: PILOT (free) < EXPORT ($49) < DISPATCH ($149) < ADMIN
Tokens: HMAC-SHA256 signed, 7-day expiry, revocable
Format: `PILOT-{hex}.{sig}`

## Ports

| Service | Port | Process |
|---------|------|---------|
| solvulator backend | :9800 | python3 src/system.py |
| legal warroom UI | :8340 | bb serve.bb |
| solvulator warroom | :19421 | bb (grafanadesk) |
| myclaizer | :19422 | node (vite) |
| legal-warroom | :4444 | node |

## File Layout

```
~/solvulator/                    ← canonical repo
├── src/system.py                ← unified backend (all logic here)
├── agents/pipeline.py           ← 12-agent definitions + CLI runner
├── agents/*.md                  ← agent system prompts (reference docs)
├── static/index.html            ← solvulator UI
├── static/projects.html         ← project gallery
├── pipeline.edn                 ← pipeline spec (EDN, reference)
├── test/sample.csv              ← test data
├── artifacts/                   ← pipeline run outputs
├── uploads/                     ← uploaded documents
├── ARCHITECTURE.md              ← this file
├── CLAUDE.md                    ← project instructions
└── .env.example                 ← key template
```

## Key Design Decisions

1. **No framework** — stdlib http.server only. ~1000 lines total.
2. **No database** — in-memory dicts + filesystem artifacts. SQLite when needed.
3. **Single process** — one Python process handles everything.
4. **API keys server-side only** — never sent to browser.
5. **Hebrew-first** — all agent prompts in Hebrew, outputs bilingual where needed.
6. **Human gates** — pipeline pauses, never auto-dispatches legal documents.
7. **Fallback chain** — if one LLM provider fails, try the next.
