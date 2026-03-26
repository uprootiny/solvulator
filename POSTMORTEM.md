# Solvulator Postmortems & Constraints

## What We Tried, What Failed, Why

### PM-1: Scattered Codebases (Mar 24-26)

**What happened:** Solvulator code ended up in 4 locations:
- `~/grafanadesk/` (bb/clj version, git repo, wrong name)
- `~/metaops/solvulator/` (python version, no git)
- `~/dmkdird/` (hybrid python+clj, no git)
- `~/March2026/legalcasework/` (related but separate)

**Why:** Each Claude Code session started from a different working directory and built in place. No canonical repo existed, so each session bootstrapped its own copy. The grafanadesk repo was reused because it happened to be the active tmux session.

**Constraint:** Sessions don't share state. Each one sees a filesystem snapshot and builds locally. Without a single source of truth, copies proliferate.

**Resolution:** Consolidated to `~/solvulator/` with system.py as canonical backend.

---

### PM-2: Two Stacks, Neither Complete (Mar 24-25)

**What happened:** Built both a Clojure/bb stack (http-kit server, pipeline.edn, DataScript schema) and a Python stack (system.py, pipeline.py) for the same pipeline. The bb server served the UI but couldn't call LLMs. The Python backend had LLM integration but no UI serving.

**Why:** First session started in Clojure (user preference), second session switched to Python when LLM API calls needed stdlib HTTP. Neither was killed — both kept running on different ports.

**Constraint:** bb/http-kit doesn't have a trivial way to call OpenRouter/Gemini APIs with retry/fallback. Python stdlib does it in 20 lines. But the UI was already built against the bb API shape.

**Resolution:** Python backbone + static UI serving. BB relegated to legacy UI shells.

---

### PM-3: No API Keys in Environment (Mar 25-26)

**What happened:** Every backend startup showed `anthropic: not set, gemini: not set`. Pipeline couldn't actually run. Multiple sessions attempted to set up key management differently.

**Why:** Keys exist in `~/.env.openrouter` but system.py only reads that file for OPENROUTER_KEY and GEMINI_KEY, not for ANTHROPIC_KEY. Environment variables were never exported in the shell profile. Each session discovered this independently.

**Constraint:** Security tension — keys shouldn't be in git, shouldn't be in env for all processes, but need to reach the backend. The `/api/key` runtime injection endpoint was added as a workaround but requires UI interaction.

**Resolution:** system.py now reads all three keys from `~/.env.openrouter` at startup. Keys exist there (OpenRouter + Gemini confirmed set).

---

### PM-4: Port Collisions (Mar 25-26)

**What happened:** Multiple services claimed the same ports across sessions. `OSError: Address already in use` on :9800. Both bb and Python tried to serve on :19421. Port 8340 was reassigned from legalcasework static server to bb serve.bb.

**Why:** No port registry. Each session picked ports based on DRILL.md or defaults, without checking what was already running.

**Constraint:** Single-machine deployment with 20+ services competing for ports. No orchestrator (no docker-compose for the solvulator stack, no process manager).

**Resolution:** ARCHITECTURE.md now documents port assignments. Still need a process manager or startup script.

---

### PM-5: UI/Backend Response Format Mismatch (Mar 26)

**What happened:** The HTML UI's `streamClaude()` expected `{content: [{text: "..."}]}` (raw Anthropic format) but system.py's `/agent/claude` returns `{text: "..."}` (simplified). LLM responses showed as empty in the UI.

**Why:** UI was built against direct Anthropic API responses. Backend was built to normalize responses across providers. Neither side documented the contract.

**Constraint:** No API contract specification between frontend and backend. Changes on one side silently break the other.

**Resolution:** Other session patched the UI to handle both formats. Proper fix: document the response schema in ARCHITECTURE.md.

---

### PM-6: Google Sheets Integration Incomplete

**What happened:** SheetEngine exists and can parse CSV, but the actual Google Sheets URL (SHEET_URL) was never configured. Sheet state always shows "initializing". The rich CSV with 30+ columns (from the other session's work) doesn't match the simplified 7-column schema.

**Why:** The sheet schema evolved across sessions — started with 7 columns (minimal), expanded to 30+ (full pipeline state), then got simplified back. No migration path between schemas.

**Constraint:** Google Sheets published CSV URLs are read-only and schema changes require manual Sheet editing. The SheetEngine's REQUIRED_COLUMNS validation rejects anything that doesn't match exactly.

**Resolution:** Need to either relax validation or publish a canonical Sheet matching the expected schema.

---

## Most Impactful Constraints (Ranked)

### Tier 1: Blocking Progress

| # | Constraint | Impact | Pushable? |
|---|-----------|--------|-----------|
| C1 | **No API keys in default env** | Can't test any LLM calls, can't validate pipeline | YES — keys exist in ~/.env.openrouter, just need proper loading |
| C2 | **Multi-session divergence** | Work duplicated, copies diverge, conflicts on ports/files | YES — consolidated to ~/solvulator |
| C3 | **No process manager** | Services die silently, ports conflict, no restart | YES — write a simple start/stop script |

### Tier 2: Slowing Progress

| # | Constraint | Impact | Pushable? |
|---|-----------|--------|-----------|
| C4 | **No API contract between UI and backend** | Silent breakage on response format changes | YES — document in ARCHITECTURE.md |
| C5 | **Sheet schema instability** | CSV validation fails, sheet engine stuck at "initializing" | YES — pin the schema, publish matching Sheet |
| C6 | **Two language stacks (Clojure + Python)** | Context switching, duplicated logic, unclear which is canonical | DONE — Python is canonical |

### Tier 3: Latent Risk

| # | Constraint | Impact | Pushable? |
|---|-----------|--------|-----------|
| C7 | **In-memory state only** | Pipeline runs lost on restart | LATER — add SQLite when needed |
| C8 | **Single-process architecture** | LLM calls block HTTP server during pipeline runs | LATER — threading is already used for sheet reload |
| C9 | **No tests for LLM integration** | Can't verify pipeline without manual testing | YES — add dry-run mode with mock responses |

---

## Triage: What to Push Against Now

### Push NOW (unblocks everything else):

1. **C1: Get LLM calls working** — Keys are in ~/.env.openrouter. System.py loads them. Kill the stale :9800 process, start fresh from ~/solvulator, test with `curl -X POST localhost:9800/agent/gemini -H 'Content-Type: application/json' -d '{"prompt":"hello"}'`.

2. **C3: Write a start script** — Single `./start.sh` that kills stale processes, starts system.py on :9800, logs to artifacts/server.log. Replaces the tmux-per-service chaos.

3. **C5: Pin the sheet schema** — Commit to the 7-column schema (SV_ID, Stage, Status, Source, Document_Type, Urgency, Amount). Publish a Google Sheet with this exact header. Configure SHEET_URL.

### Push NEXT (once pipeline runs):

4. **C4: Document API contract** — Response shapes for /agent/*, /pipeline/* endpoints.
5. **C9: Dry-run pipeline test** — Mock LLM responses to validate the 12-step flow without burning API credits.

### Accept for now:

6. **C7, C8** — In-memory state and single-process are fine for pilot. SQLite and proper async can wait until there's actual user traffic.
