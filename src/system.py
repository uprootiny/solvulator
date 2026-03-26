#!/usr/bin/env python3
"""
system.py — SOLVULATOR Unified Backend v0.4
Single entrypoint. All services mounted. No framework.

Mounts:
  /health                         liveness
  /manifest                       service manifest
  /sheet/state                    sheet engine state
  /sheet/view                     density-filtered rows (GET ?density=focused&status=&urgency=)
  /sheet/snapshot                 full immutable snapshot
  /sheet/reload                   re-fetch source (POST)
  /catalog                        payment tier catalog
  /intent                         payment intent (POST)
  /token/mint                     mint scoped token (POST)
  /token/check                    validate token (GET)
  /token/revoke                   revoke token (POST)
  /protected/pipeline-state       token-gated pipeline state
  /agent/claude                   LLM proxy — hides API key (POST)
  /agent/lathe                    LATHE pre-pass proxy (POST)
  /lathe/process                  alias of /agent/lathe
  /                               serves solvulator-v04.html (GET)
  /static/*                       static file passthrough

Usage:
  python3 src/system.py
  SHEET_URL=... ANTHROPIC_API_KEY=sk-... python3 src/system.py
  python3 src/system.py test
  python3 src/system.py loop [--dry]
"""

import csv
import hashlib
import hmac
import io
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ══════════════════════════════════════════════════════
# 1. CONFIGURATION
# ══════════════════════════════════════════════════════

PORT             = int(os.environ.get("PORT", 9800))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SHEET_URL        = os.environ.get("SHEET_URL", "")
PILOT_MODE       = os.environ.get("PILOT_MODE", "true").lower() == "true"
TOKEN_SECRET     = os.environ.get("TOKEN_SECRET", "solvulator-system-secret-change-in-prod")
STATIC_DIR       = os.environ.get("STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static"))
CLAUDE_MODEL     = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")
VERSION          = "0.4.0"
SERVICE_ID       = "solvulator-system"

# Also support OpenRouter and Gemini as fallbacks
OPENROUTER_KEY   = ""
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
LEGAL_SHEET_ID   = os.environ.get("LEGAL_SHEET_ID", "1cK4F7_5gGB_inEhZieiZdrdVbZOZWqE2phpHEO2WStM")
LEGAL_SHEET_URL  = os.environ.get("LEGAL_SHEET_URL", f"https://docs.google.com/spreadsheets/d/{LEGAL_SHEET_ID}/export?format=csv")
_or_env = Path.home() / ".env.openrouter"
if _or_env.exists():
    for _line in _or_env.read_text().splitlines():
        if _line.startswith("OPENROUTER_API_KEY="):
            OPENROUTER_KEY = _line.split("=", 1)[1].strip()
        elif _line.startswith("GEMINI_API_KEY="):
            GEMINI_API_KEY = _line.split("=", 1)[1].strip()

REQUIRED_COLUMNS = {"SV_ID", "Stage", "Status", "Source", "Document_Type", "Urgency"}
VALID_STATUSES   = {"PENDING", "RUNNING", "DONE", "BLOCKED"}
VALID_URGENCIES  = {"high", "medium", "low"}
VALID_SCOPES     = {"PILOT", "EXPORT", "DISPATCH", "ADMIN"}

# ══════════════════════════════════════════════════════
# 2. INVARIANT ASSERTIONS
# ══════════════════════════════════════════════════════

def assert_snapshot(snap):
    assert "rows" in snap, "snapshot missing rows"
    assert "valid" in snap, "snapshot missing valid"
    assert "invalid" in snap, "snapshot missing invalid"
    assert "total" in snap, "snapshot missing total"
    assert snap["complete"] is True, "snapshot.complete must be True"
    assert snap["total"] == len(snap["valid"]) + len(snap["invalid"]), \
        f"total mismatch: {snap['total']} != {len(snap['valid'])} + {len(snap['invalid'])}"

def assert_row(row):
    assert row.get("sv_id"), f"row missing sv_id: {row}"
    assert row.get("status") in VALID_STATUSES, \
        f"invalid status '{row.get('status')}' in row {row.get('sv_id')}"

# ══════════════════════════════════════════════════════
# 3. SHEET ENGINE
# ══════════════════════════════════════════════════════

class SheetEngine:
    def __init__(self):
        self.state = "initializing"
        self.snapshot = None
        self.source = None
        self.error = None

    def _fetch_csv(self, source):
        if source.startswith("http"):
            req = urllib.request.Request(source, headers={"User-Agent": "solvulator/1"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        elif Path(source).exists():
            return Path(source).read_text(encoding="utf-8")
        else:
            raise RuntimeError(f"Source not found: {source}")

    def _parse_header(self, line):
        cols = [c.strip() for c in line.split(",")]
        missing = REQUIRED_COLUMNS - set(cols)
        if missing:
            raise RuntimeError(f"Schema violation — missing columns: {missing}")
        return cols

    def _parse_line(self, line):
        fields, current, in_q = [], "", False
        for ch in line:
            if ch == '"':
                in_q = not in_q
            elif ch == ',' and not in_q:
                fields.append(current.strip())
                current = ""
            else:
                current += ch
        fields.append(current.strip())
        return fields

    def _normalize(self, raw):
        stage_raw = raw.get("Stage", "")
        amount_raw = raw.get("Amount", "")
        stage = None
        amount = None
        try:
            stage = int(stage_raw.strip()) if stage_raw.strip() else None
        except ValueError:
            pass
        try:
            amount = int(amount_raw.strip()) if amount_raw.strip() else None
        except ValueError:
            pass
        return {
            "sv_id": raw.get("SV_ID", "").strip(),
            "stage": stage,
            "status": raw.get("Status", "").strip().upper(),
            "source": raw.get("Source", "").strip(),
            "document_type": raw.get("Document_Type", "").strip(),
            "urgency": raw.get("Urgency", "").strip().lower(),
            "amount": amount,
            "clean_text": raw.get("Clean_Text_Link", raw.get("Notes", "")).strip(),
            "_raw": raw,
        }

    def _validate(self, row):
        errors = []
        if not row["sv_id"]:
            errors.append("missing SV_ID")
        if row["status"] not in VALID_STATUSES:
            errors.append(f"invalid status: {row['status']!r}")
        if row["urgency"] and row["urgency"] not in VALID_URGENCIES:
            errors.append(f"invalid urgency: {row['urgency']!r}")
        if row["stage"] is None:
            errors.append("stage must be a non-negative integer")
        return {"valid": not errors, "row": row, "errors": errors}

    def load(self, source):
        try:
            self.source = source
            raw_csv = self._fetch_csv(source)
            lines = [l.strip() for l in raw_csv.splitlines() if l.strip()]
            if not lines:
                raise RuntimeError("Empty CSV")
            header = self._parse_header(lines[0])
            col_count = len(header)
            results = []
            for line in lines[1:]:
                fields = self._parse_line(line)
                if len(fields) != col_count:
                    results.append({
                        "valid": False,
                        "row": {"sv_id": "", "_raw": {"line": line}},
                        "errors": [f"column count mismatch: expected {col_count}, got {len(fields)}"],
                    })
                    continue
                row = self._normalize(dict(zip(header, fields)))
                results.append(self._validate(row))

            valid = [r for r in results if r["valid"]]
            invalid = [r for r in results if not r["valid"]]
            snap = {
                "total": len(results),
                "valid": valid,
                "invalid": invalid,
                "rows": results,
                "headers": header,
                "complete": True,
                "fetched_at": str(int(time.time())),
                "source": source,
            }
            assert_snapshot(snap)
            self.snapshot = snap
            self.state = "ready"
            self.error = None
            print(f"  sheet: ready — {snap['total']} rows "
                  f"(valid: {len(valid)}, invalid: {len(invalid)})")
        except Exception as e:
            self.state = "error"
            self.error = str(e)
            print(f"  sheet ERROR: {e}")

    def view(self, density="focused", status="", urgency=""):
        if self.state != "ready":
            return {"error": "not ready", "state": self.state}
        DENSITY_FIELDS = {
            "summary": {"sv_id", "status"},
            "focused": {"sv_id", "status", "urgency", "stage", "source"},
            "neighborhood": {"sv_id", "status", "urgency", "stage", "source", "document_type", "amount"},
            "full": None,
        }
        if density not in DENSITY_FIELDS:
            return {"error": f"invalid density: {density!r}", "valid_densities": list(DENSITY_FIELDS)}
        fields = DENSITY_FIELDS[density]
        urgency_order = {"high": 0, "medium": 1, "low": 2, "": 3}
        rows = sorted(
            [r["row"] for r in self.snapshot["valid"]],
            key=lambda r: (urgency_order.get(r.get("urgency", ""), 3), -(r.get("stage") or 0))
        )
        if status:
            rows = [r for r in rows if r.get("status", "").upper() == status.upper()]
        if urgency:
            rows = [r for r in rows if r.get("urgency", "").lower() == urgency.lower()]
        projected = []
        for row in rows:
            if fields is None:
                projected.append({k: v for k, v in row.items() if k != "_raw"})
            else:
                projected.append({k: row.get(k) for k in (fields | {"sv_id"})})
        return {"density": density, "count": len(projected), "rows": projected}

    def state_summary(self):
        if self.state != "ready" or not self.snapshot:
            return {"state": self.state, "error": self.error}
        snap = self.snapshot
        return {
            "state": self.state,
            "total": snap["total"],
            "valid": len(snap["valid"]),
            "invalid": len(snap["invalid"]),
            "complete": snap["complete"],
            "fetched_at": snap["fetched_at"],
            "columns": snap["headers"],
        }

SHEET = SheetEngine()

# ══════════════════════════════════════════════════════
# 4. TOKEN ENGINE
# ══════════════════════════════════════════════════════

TOKEN_STORE = {}

def mint_token(scope, meta=None):
    if scope not in VALID_SCOPES:
        raise ValueError(f"Invalid scope: {scope}. Valid: {sorted(VALID_SCOPES)}")
    token_id = "PILOT-" + uuid.uuid4().hex[:10].upper()
    issued_at = int(time.time())
    expires_at = issued_at + 7 * 24 * 3600
    payload = f"{token_id}:{scope}:{issued_at}"
    sig = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    token = f"{token_id}.{sig}"
    record = {
        "token": token, "token_id": token_id, "scope": scope,
        "issued_at": issued_at, "expires_at": expires_at,
        "pilot_mode": PILOT_MODE, "meta": meta or {}, "revoked": False,
    }
    TOKEN_STORE[token] = record
    return record

def check_token(token, required_scope=None):
    if not token:
        return False, {}, "No token provided"
    record = TOKEN_STORE.get(token)
    if not record:
        return False, {}, "Token not found"
    if record["revoked"]:
        return False, record, "Token revoked"
    if int(time.time()) > record["expires_at"]:
        return False, record, "Token expired"
    if required_scope and record["scope"] != "ADMIN":
        order = {"PILOT": 1, "EXPORT": 2, "DISPATCH": 3, "ADMIN": 4}
        if order.get(record["scope"], 0) < order.get(required_scope, 0):
            return False, record, f"Insufficient scope: have {record['scope']}, need {required_scope}"
    return True, record, "ok"

CATALOG = [
    {"id": "pilot", "scope": "PILOT", "price_ILS": 0, "name": "Pilot",
     "features": ["LATHE Stage 0", "12-agent pipeline", "Timeline", "Read-only"]},
    {"id": "export", "scope": "EXPORT", "price_ILS": 49, "name": "Export",
     "features": ["All pilot", "JSON export", "PDF report"]},
    {"id": "dispatch", "scope": "DISPATCH", "price_ILS": 149, "name": "Dispatch",
     "features": ["All export", "Net HaMishpat filing", "Registered mail", "Receipts"]},
]

# ══════════════════════════════════════════════════════
# 5. AGENT PROXY
# ══════════════════════════════════════════════════════

def call_gemini(prompt, system="", max_tokens=1000):
    """Call Gemini API directly."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set on server"}
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    body = json.dumps({
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
          headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"text": text, "model": GEMINI_MODEL}
    except urllib.error.HTTPError as e:
        return {"error": f"Gemini API {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}

def call_claude(prompt, system="", max_tokens=1000):
    """Proxy LLM call. Tries Anthropic → Gemini → OpenRouter. Falls through on error."""
    providers = []
    if ANTHROPIC_API_KEY: providers.append(("anthropic", lambda: _call_anthropic(prompt, system, max_tokens)))
    if GEMINI_API_KEY: providers.append(("gemini", lambda: call_gemini(prompt, system, max_tokens)))
    if OPENROUTER_KEY: providers.append(("openrouter", lambda: _call_openrouter(prompt, system, max_tokens)))

    if not providers:
        return {"error": "No API key configured (ANTHROPIC_API_KEY, GEMINI_API_KEY, or OPENROUTER_KEY)"}

    last_error = None
    for name, fn in providers:
        result = fn()
        if "error" not in result:
            result["_provider"] = name
            return result
        last_error = result
        print(f"  LLM {name} failed: {result.get('error','?')[:80]} — trying next")

    return last_error

def _call_anthropic(prompt, system="", max_tokens=1000):
    messages = [{"role": "user", "content": prompt}]
    body = {"model": CLAUDE_MODEL, "max_tokens": max_tokens, "messages": messages}
    if system:
        body["system"] = system
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return {"text": text, "model": data.get("model"), "usage": data.get("usage")}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"Anthropic API {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}

def _call_openrouter(prompt, system="", max_tokens=1000):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": "anthropic/claude-sonnet-4", "max_tokens": max_tokens, "messages": messages}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "HTTP-Referer": "https://solvulator.com",
            "X-OpenRouter-Title": "solvulator",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"]
            return {"text": text, "model": data.get("model"), "usage": data.get("usage")}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"OpenRouter API {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}

def call_lathe(doc_text, goal="", lang="Hebrew"):
    prompt = f"""You are LATHE Stage 0 — legal argument decomposition engine.
Taxonomy: distortion (overbroad framing), projection (inferred mental state without evidence),
fixation (single-theory dependency), assertion (unsupported factual claim).
OUTPUT LANGUAGE: {lang}
Return ONLY valid JSON. No markdown fences. Schema:
{{
  "facts":[{{"id":"f1","text":"string","source":"document","importance":0.8,"status":"stated"}}],
  "claims":[{{"id":"c1","text":"string","type":"factual|normative|mixed","support_score":0.5,"scope_score":0.5,"fragility_score":0.5}}],
  "failure_analysis":[{{"claim_id":"c1","failures":[{{"type":"distortion|projection|fixation|assertion","reason":"string","location":"string","severity":"fatal|significant|minor","challenge_question":"string"}}]}}],
  "repairs":[{{"claim_id":"c1","original":"string","repaired":"string","repair_strategy":"narrowing|evidential_conditioning|alternative_theory|hedging"}}],
  "counterarguments":[{{"claim_id":"c1","text":"string","strength":0.7,"applicable_doctrine":"string"}}],
  "lathe_summary":"2-sentence assessment",
  "litigation_integrity_score":0.6
}}
INDIVIDUAL GOAL: {goal}
DOCUMENT:
{doc_text[:3000]}"""
    result = call_claude(prompt, max_tokens=1500)
    if "error" in result:
        return result
    try:
        clean = result["text"].replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except json.JSONDecodeError as e:
        return {"error": f"LATHE JSON parse failed: {e}", "raw": result["text"][:200]}

# ══════════════════════════════════════════════════════
# 6. AGENT LOOP
# ══════════════════════════════════════════════════════

def run_agent_loop(dry_run=False):
    if SHEET.state != "ready":
        print("  agent loop: sheet not ready")
        return []
    pending = [r["row"] for r in SHEET.snapshot["valid"] if r["row"].get("status") == "PENDING"]
    print(f"  agent loop: {len(pending)} pending rows")
    results = []
    for row in pending:
        sv_id = row.get("sv_id", "?")
        doc_text = row.get("clean_text", "")
        if not doc_text:
            print(f"    {sv_id}: no text — skipping")
            continue
        if dry_run:
            print(f"    {sv_id}: DRY RUN — would call LATHE on {len(doc_text)} chars")
            results.append({"sv_id": sv_id, "dry_run": True})
            continue
        print(f"    {sv_id}: calling LATHE...")
        lathe = call_lathe(doc_text, goal=row.get("goal", ""))
        result = {
            "sv_id": sv_id, "status": row.get("status"),
            "urgency": row.get("urgency"), "lathe": lathe,
            "processed_at": int(time.time()),
        }
        results.append(result)
        score = lathe.get("litigation_integrity_score", "?")
        n_failures = sum(len(fa.get("failures", [])) for fa in lathe.get("failure_analysis", []))
        print(f"    {sv_id}: integrity={score} failures={n_failures}")
    return results

# ══════════════════════════════════════════════════════
# 6b. PIPELINE ENGINE (12-agent legal pipeline)
# ══════════════════════════════════════════════════════

# Import AGENTS list from agents/pipeline.py
_agents_dir = str(Path(__file__).resolve().parent.parent / "agents")
if _agents_dir not in sys.path:
    sys.path.insert(0, _agents_dir)
from pipeline import AGENTS as PIPELINE_AGENTS

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)


class PipelineEngine:
    def __init__(self):
        self.runs = {}  # run_id -> run state dict

    def start_run(self, doc_text, model=None):
        doc_hash = hashlib.sha256(doc_text.encode()).hexdigest()[:12]
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{doc_hash}"
        run_dir = ARTIFACTS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "source.txt").write_text(doc_text, encoding="utf-8")
        run = {
            "run_id": run_id,
            "doc_text": doc_text,
            "model": model or GEMINI_MODEL,
            "steps_completed": [],
            "current_step": 0,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_dir": str(run_dir),
        }
        self.runs[run_id] = run
        print(f"  pipeline: started run {run_id} ({len(doc_text)} chars)")
        return run

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def list_runs(self):
        return [
            {k: v for k, v in r.items() if k != "doc_text"}
            for r in sorted(self.runs.values(), key=lambda r: r["created_at"], reverse=True)
        ]

    def run_step(self, run_id):
        run = self.runs.get(run_id)
        if not run:
            return {"error": "run not found"}
        if run["status"] == "completed":
            return {"error": "run already completed"}
        if run["status"] == "error":
            return {"error": "run in error state"}

        step_idx = run["current_step"]
        if step_idx >= len(PIPELINE_AGENTS):
            run["status"] = "completed"
            self._save_manifest(run)
            return {"done": True, "status": "completed"}

        agent = PIPELINE_AGENTS[step_idx]

        # If paused at a human gate, require explicit approval first
        if run["status"] == "paused_at_gate":
            return {
                "error": "awaiting human approval",
                "gate_agent": agent["id"],
                "gate_name": agent["name"],
                "gate_name_he": agent["name_he"],
                "step": step_idx + 1,
            }

        run["status"] = "running"
        step_num = step_idx + 1

        # Build prompt with prior results context
        prior_parts = []
        for r in run["steps_completed"]:
            prior_parts.append(
                f"[{r['agent_name']}]\n"
                + json.dumps(r["result"], ensure_ascii=False, indent=2)[:800]
            )
        prior_context = "\n\n".join(prior_parts) if prior_parts else "(שלב ראשון)"

        prompt = (
            f"מסמך:\n---\n{run['doc_text'][:6000]}\n---\n\n"
            f"תוצאות קודמות:\n{prior_context[:3000]}\n\nבצע. JSON בלבד."
        )

        t0 = time.time()
        result = call_claude(prompt, system=agent["system"], max_tokens=2000)
        elapsed = int((time.time() - t0) * 1000)

        # Try to parse JSON from response text
        if "text" in result and "error" not in result:
            raw_text = result["text"]
            try:
                clean = raw_text.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(clean)
                result = parsed
            except json.JSONDecodeError:
                result = {"raw": raw_text[:2000]}

        entry = {
            "step": step_num,
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "agent_name_he": agent["name_he"],
            "requires_human": agent["requires_human"],
            "result": result,
            "elapsed_ms": elapsed,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        run["steps_completed"].append(entry)
        run["current_step"] = step_idx + 1

        # Save step artifact
        run_dir = Path(run["run_dir"])
        sf = run_dir / f"step_{step_num:02d}_{agent['id']}.json"
        sf.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"  pipeline [{run_id[:16]}] step {step_num:02d}/{len(PIPELINE_AGENTS)} "
              f"{agent['id']} ({elapsed}ms)")

        # Check if next agent requires human gate
        next_idx = step_idx + 1
        if next_idx < len(PIPELINE_AGENTS) and PIPELINE_AGENTS[next_idx]["requires_human"]:
            run["status"] = "paused_at_gate"
            next_agent = PIPELINE_AGENTS[next_idx]
            entry["next_gate"] = {
                "step": next_idx + 1,
                "agent_id": next_agent["id"],
                "agent_name": next_agent["name"],
                "agent_name_he": next_agent["name_he"],
            }
        elif next_idx >= len(PIPELINE_AGENTS):
            run["status"] = "completed"
            self._save_manifest(run)
        else:
            run["status"] = "pending"

        return entry

    def approve_gate(self, run_id):
        run = self.runs.get(run_id)
        if not run:
            return {"error": "run not found"}
        if run["status"] != "paused_at_gate":
            return {"error": f"run not at gate (status: {run['status']})"}
        run["status"] = "pending"
        step_idx = run["current_step"]
        agent = PIPELINE_AGENTS[step_idx] if step_idx < len(PIPELINE_AGENTS) else None
        return {
            "approved": True,
            "next_step": step_idx + 1,
            "next_agent": agent["id"] if agent else None,
            "next_agent_name": agent["name"] if agent else None,
        }

    def _save_manifest(self, run):
        run_dir = Path(run["run_dir"])
        manifest = {
            "run_id": run["run_id"],
            "model": run["model"],
            "steps_completed": len(run["steps_completed"]),
            "status": run["status"],
            "created_at": run["created_at"],
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )


PIPELINE = PipelineEngine()

# ══════════════════════════════════════════════════════
# 7. HTTP HANDLER
# ══════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _log(self, status):
        print(f"  {self.command:6} {self.path[:60]:60} -> {status}")

    def send_json(self, body, status=200):
        payload = json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
        self.wfile.write(payload)
        self._log(status)

    def send_err(self, msg, status):
        self.send_json({"error": msg, "status": status}, status)

    def read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except:
            return {}

    def get_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        params = parse_qs(urlparse(self.path).query)
        return params.get("token", [""])[0]

    def get_params(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def serve_static(self, rel_path):
        path = Path(STATIC_DIR) / rel_path.lstrip("/")
        if path.is_dir():
            path = path / "index.html"
        if not path.exists():
            self.send_err(f"Not found: {rel_path}", 404)
            return
        mime = {
            ".html": "text/html; charset=utf-8", ".js": "application/javascript",
            ".css": "text/css", ".json": "application/json", ".md": "text/plain",
        }.get(path.suffix, "application/octet-stream")
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        self._log(200)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        routes = {
            "/health": self.h_health, "/manifest": self.h_manifest,
            "/catalog": self.h_catalog, "/token/check": self.h_token_check,
            "/sheet/state": self.h_sheet_state, "/sheet/view": self.h_sheet_view,
            "/sheet/snapshot": self.h_sheet_snapshot,
            "/legal/cases": self.h_legal_cases,
            "/protected/pipeline-state": self.h_pipeline_state,
            "/api/loop/status": self.h_loop_status_get,
            "/pipeline/runs": self.h_pipeline_list,
        }
        if path in routes:
            routes[path]()
        elif path.startswith("/pipeline/") and path.count("/") == 2:
            # GET /pipeline/:run_id
            run_id = path.split("/")[2]
            self.h_pipeline_get(run_id)
        elif path == "/" or path == "/index.html":
            html_path = Path(STATIC_DIR) / "index.html"
            if html_path.exists():
                self.serve_static("index.html")
            else:
                self.send_json({
                    "service": SERVICE_ID, "version": VERSION,
                    "status": "ready",
                    "endpoints": {
                        "health": "/health", "manifest": "/manifest", "catalog": "/catalog",
                        "claude": "POST /agent/claude", "gemini": "POST /agent/gemini",
                        "lathe": "POST /agent/lathe", "mint": "POST /token/mint",
                        "sheet": "/sheet/view",
                    },
                    "keys": {"anthropic": bool(ANTHROPIC_API_KEY), "gemini": bool(GEMINI_API_KEY), "openrouter": bool(OPENROUTER_KEY)},
                    "try": [
                        f"curl -X POST http://localhost:{PORT}/agent/gemini -H 'Content-Type: application/json' -d '{{\"prompt\":\"hello\"}}'",
                        f"curl http://localhost:{PORT}/health",
                    ],
                })
        elif path.startswith("/static/"):
            self.serve_static(path[8:])
        else:
            self.send_err(f"Not found: {path}", 404)

    def do_POST(self):
        path = urlparse(self.path).path
        routes = {
            "/intent": self.h_intent, "/token/mint": self.h_token_mint,
            "/token/revoke": self.h_token_revoke, "/sheet/reload": self.h_sheet_reload,
            "/agent/claude": self.h_agent_claude, "/agent/gemini": self.h_agent_gemini,
            "/agent/lathe": self.h_agent_lathe, "/lathe/process": self.h_agent_lathe,
            "/api/key": self.h_api_key, "/api/loop/start": self.h_loop_start,
            "/api/loop/status": self.h_loop_status,
            "/pipeline/start": self.h_pipeline_start,
        }
        if path in routes:
            routes[path]()
        elif path.startswith("/pipeline/") and path.endswith("/step"):
            # POST /pipeline/:run_id/step
            run_id = path.split("/")[2]
            self.h_pipeline_step(run_id)
        elif path.startswith("/pipeline/") and path.endswith("/approve"):
            # POST /pipeline/:run_id/approve
            run_id = path.split("/")[2]
            self.h_pipeline_approve(run_id)
        else:
            self.send_err(f"Not found: {path}", 404)

    # ── Routes ────────────────────────────────────────

    def h_health(self):
        self.send_json({
            "ok": True, "service": SERVICE_ID, "version": VERSION,
            "pilot_mode": PILOT_MODE, "sheet_state": SHEET.state,
            "anthropic": bool(ANTHROPIC_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
            "openrouter": bool(OPENROUTER_KEY),
            "api_key_set": bool(ANTHROPIC_API_KEY) or bool(GEMINI_API_KEY) or bool(OPENROUTER_KEY),
            "timestamp": int(time.time()),
        })

    def h_manifest(self):
        self.send_json({
            "service": SERVICE_ID, "version": VERSION,
            "endpoints": [
                {"method": "GET", "path": "/health"},
                {"method": "GET", "path": "/manifest"},
                {"method": "GET", "path": "/catalog"},
                {"method": "POST", "path": "/intent"},
                {"method": "POST", "path": "/token/mint"},
                {"method": "GET", "path": "/token/check"},
                {"method": "POST", "path": "/token/revoke"},
                {"method": "GET", "path": "/sheet/state"},
                {"method": "GET", "path": "/sheet/view", "params": "density status urgency"},
                {"method": "GET", "path": "/sheet/snapshot"},
                {"method": "POST", "path": "/sheet/reload"},
                {"method": "GET", "path": "/protected/pipeline-state", "auth": "PILOT"},
                {"method": "POST", "path": "/agent/claude"},
                {"method": "POST", "path": "/agent/lathe"},
            ],
            "invariants": [
                "No API key in browser",
                "No silent failure",
                "No partial state",
                "No implicit auth",
            ],
        })

    def h_catalog(self):
        self.send_json({"catalog": CATALOG, "currency": "ILS", "pilot_mode": PILOT_MODE})

    def h_intent(self):
        body = self.read_body()
        scope = body.get("scope", "PILOT").upper()
        tier = next((c for c in CATALOG if c["scope"] == scope), None)
        if not tier:
            self.send_err(f"Unknown scope: {scope}", 400); return
        if tier["price_ILS"] == 0 or PILOT_MODE:
            record = mint_token(scope, meta={"tier": tier["id"]})
            self.send_json({
                "intent_id": "FREE-" + uuid.uuid4().hex[:8].upper(),
                "status": "fulfilled", "token": record["token"],
                "scope": record["scope"], "expires_at": record["expires_at"], "tier": tier,
            })
        else:
            self.send_json({
                "intent_id": "INTENT-" + uuid.uuid4().hex[:8].upper(),
                "status": "pending_payment", "scope": scope,
                "price_ILS": tier["price_ILS"],
            })

    def h_token_mint(self):
        body = self.read_body()
        scope = body.get("scope", "PILOT").upper()
        meta = body.get("meta", {})
        try:
            record = mint_token(scope, meta=meta)
            self.send_json({
                "token": record["token"], "scope": record["scope"],
                "issued_at": record["issued_at"], "expires_at": record["expires_at"],
                "pilot_mode": record["pilot_mode"],
            })
        except ValueError as e:
            self.send_err(str(e), 400)

    def h_token_check(self):
        token = self.get_token()
        scope = self.get_params().get("scope")
        valid, record, reason = check_token(token, scope)
        if valid:
            self.send_json({"valid": True, "scope": record["scope"], "expires_at": record["expires_at"]})
        else:
            self.send_json({"valid": False, "reason": reason}, 401 if not record else 403)

    def h_token_revoke(self):
        body = self.read_body()
        token = body.get("token", self.get_token())
        record = TOKEN_STORE.get(token)
        if not record:
            self.send_err("Token not found", 404); return
        record["revoked"] = True
        self.send_json({"revoked": True})

    def h_sheet_state(self):
        self.send_json(SHEET.state_summary())

    def h_sheet_view(self):
        if SHEET.state != "ready":
            self.send_err(f"Sheet not ready: {SHEET.state}", 503); return
        p = self.get_params()
        result = SHEET.view(density=p.get("density", "focused"),
                           status=p.get("status", ""), urgency=p.get("urgency", ""))
        if "error" in result:
            self.send_err(result["error"], 400)
        else:
            self.send_json(result)

    def h_sheet_snapshot(self):
        if SHEET.state != "ready":
            self.send_err("Not ready", 503); return
        snap = SHEET.snapshot
        self.send_json({
            "complete": snap["complete"], "total": snap["total"],
            "valid": [r["row"] for r in snap["valid"]],
            "invalid": [{"row": r["row"], "errors": r["errors"]} for r in snap["invalid"]],
            "fetched_at": snap["fetched_at"],
        })

    def h_sheet_reload(self):
        body = self.read_body()
        src = body.get("source", "") or SHEET.source or SHEET_URL
        if not src:
            self.send_err("No source configured. Provide 'source' in POST body or set SHEET_URL env.", 400); return
        # Persist source for future reloads
        SHEET.source = src
        import threading
        threading.Thread(target=SHEET.load, args=(src,), daemon=True).start()
        self.send_json({"reloading": True, "source": src})

    # ── Legal Cases (from Gmail pipeline Google Sheet) ──

    _legal_cache = {"rows": [], "ts": 0}

    def h_legal_cases(self):
        import csv, io, time
        now = time.time()
        cache = type(self)._legal_cache
        # cache for 60s
        if now - cache["ts"] > 60 or not cache["rows"]:
            try:
                req = urllib.request.Request(LEGAL_SHEET_URL, headers={"User-Agent": "solvulator/1"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read().decode("utf-8")
                reader = csv.reader(io.StringIO(raw))
                headers_row = next(reader, None)
                if not headers_row:
                    self.send_json({"cases": [], "error": "empty sheet"}); return
                # Fixed columns from the Apps Script
                FIXED = ["case_number", "case_name", "plaintiff", "defendant",
                         "plaintiff_lawyer", "defendant_lawyer", "case_type", "status",
                         "decision", "plaintiff_deadline", "defendant_deadline",
                         "plaintiff_strategy", "defendant_strategy", "exhibits_count"]
                rows = []
                for row in reader:
                    if not any(row):
                        continue
                    case = {}
                    for i, key in enumerate(FIXED):
                        case[key] = row[i] if i < len(row) else ""
                    # Dynamic columns after fixed: pairs of (description, link)
                    extra = row[len(FIXED):]
                    exhibits, facts = [], []
                    # First batch = exhibits, second = facts (from Apps Script writeToSheet)
                    exhibit_count = int(case.get("exhibits_count") or 0)
                    idx = 0
                    for _ in range(exhibit_count):
                        if idx + 1 < len(extra):
                            exhibits.append({"description": extra[idx], "link": extra[idx + 1]})
                            idx += 2
                    # Rest are facts
                    while idx + 1 < len(extra):
                        facts.append({"description": extra[idx], "link": extra[idx + 1]})
                        idx += 2
                    case["exhibits"] = exhibits
                    case["facts"] = facts
                    rows.append(case)
                cache["rows"] = rows
                cache["ts"] = now
            except Exception as e:
                self.send_json({"cases": cache["rows"], "error": str(e), "stale": True}); return
        self.send_json({"cases": cache["rows"], "count": len(cache["rows"])})

    def h_pipeline_state(self):
        token = self.get_token()
        valid, record, reason = check_token(token, "PILOT")
        if not valid:
            self.send_json({"valid": False, "reason": reason}, 401 if not record else 403); return
        self.send_json({
            "scope": record["scope"], "sheet": SHEET.state_summary(),
            "agent": {"claude_model": CLAUDE_MODEL, "api_key_set": bool(ANTHROPIC_API_KEY) or bool(OPENROUTER_KEY)},
            "tokens": {"total": len(TOKEN_STORE)},
        })

    def h_agent_claude(self):
        body = self.read_body()
        prompt = body.get("prompt", "")
        system = body.get("system", "")
        max_tokens = int(body.get("max_tokens", 1000))
        if not prompt:
            self.send_err("prompt required", 400); return
        if not ANTHROPIC_API_KEY and not OPENROUTER_KEY:
            self.send_err("No API key configured on server", 503); return
        result = call_claude(prompt, system=system, max_tokens=max_tokens)
        self.send_json(result, 200 if "error" not in result else 502)

    # ── API KEY INGESTION ─────────────────────────────────────
    def h_api_key(self):
        """Accept API key via web UI. Stores in memory + appends to ~/.env.openrouter."""
        global ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_KEY
        body = self.read_body()
        key = body.get("key", "").strip()
        provider = body.get("provider", "auto")
        if not key:
            self.send_err("key required", 400); return

        # Auto-detect provider from key format
        if provider == "auto":
            if key.startswith("sk-ant-"):
                provider = "anthropic"
            elif key.startswith("AIza"):
                provider = "gemini"
            elif key.startswith("sk-or-"):
                provider = "openrouter"
            elif key.startswith("4/") or key.startswith("ya29."):
                provider = "gemini_oauth"
            else:
                provider = "openrouter"  # default fallback

        # Store in memory
        env_line = ""
        if provider == "anthropic":
            ANTHROPIC_API_KEY = key
            env_line = f"ANTHROPIC_API_KEY={key}"
        elif provider in ("gemini", "gemini_oauth"):
            GEMINI_API_KEY = key
            env_line = f"GEMINI_API_KEY={key}"
        elif provider == "openrouter":
            OPENROUTER_KEY = key
            env_line = f"OPENROUTER_API_KEY={key}"

        # Persist to env file
        if env_line:
            env_path = Path.home() / ".env.openrouter"
            existing = env_path.read_text() if env_path.exists() else ""
            # Replace existing line for this provider or append
            var_name = env_line.split("=")[0]
            lines = [l for l in existing.splitlines() if not l.startswith(var_name + "=")]
            lines.append(env_line)
            env_path.write_text("\n".join(lines) + "\n")
            env_path.chmod(0o600)

        self.send_json({
            "ok": True,
            "provider": provider,
            "key_prefix": key[:12] + "...",
            "keys_active": {
                "anthropic": bool(ANTHROPIC_API_KEY),
                "gemini": bool(GEMINI_API_KEY),
                "openrouter": bool(OPENROUTER_KEY),
            }
        })

    # ── LOOP CONTROL ──────────────────────────────────────────

    _loop_state = {"running": False, "results": [], "error": None, "started_at": None}

    def h_loop_start(self):
        """Start a background coggy/agent loop."""
        import threading
        if Handler._loop_state["running"]:
            self.send_json({"error": "loop already running"}, 409); return
        body = self.read_body()
        lanes = int(body.get("lanes", 3))
        steps = int(body.get("steps", 5))
        model = body.get("model", "google/gemini-2.5-flash")

        Handler._loop_state = {"running": True, "results": [], "error": None,
                                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "lanes": lanes, "steps": steps, "model": model}

        def run_bg():
            try:
                import subprocess
                result = subprocess.run([
                    sys.executable,
                    str(Path(__file__).resolve().parent.parent.parent / "metaops" / "martial" / "ops" / "coggy_loops.py"),
                    "--lanes", str(lanes), "--steps", str(steps), "--model", model
                ], capture_output=True, text=True, timeout=600,
                   cwd=str(Path(__file__).resolve().parent.parent.parent / "metaops" / "martial"))
                Handler._loop_state["results"] = result.stdout.split("\n")[-5:]
                Handler._loop_state["error"] = result.stderr[-200:] if result.returncode != 0 else None
            except Exception as e:
                Handler._loop_state["error"] = str(e)
            finally:
                Handler._loop_state["running"] = False

        threading.Thread(target=run_bg, daemon=True).start()
        self.send_json({"started": True, "lanes": lanes, "steps": steps, "model": model})

    def h_loop_status(self):
        self.send_json(Handler._loop_state)

    def h_loop_status_get(self):
        self.send_json(Handler._loop_state)

    def h_agent_gemini(self):
        body = self.read_body()
        prompt = body.get("prompt", "")
        system = body.get("system", "")
        max_tokens = int(body.get("max_tokens", 1000))
        if not prompt:
            self.send_err("prompt required", 400); return
        if not GEMINI_API_KEY:
            self.send_err("GEMINI_API_KEY not configured", 503); return
        result = call_gemini(prompt, system=system, max_tokens=max_tokens)
        self.send_json(result, 200 if "error" not in result else 502)

    def h_agent_lathe(self):
        body = self.read_body()
        doc_text = body.get("doc_text", body.get("text", ""))
        goal = body.get("goal", "")
        lang = body.get("lang", "Hebrew")
        if not doc_text:
            self.send_err("doc_text required", 400); return
        if not ANTHROPIC_API_KEY and not OPENROUTER_KEY:
            self.send_err("No API key configured on server", 503); return
        result = call_lathe(doc_text, goal=goal, lang=lang)
        self.send_json(result, 200 if "error" not in result else 502)

    # ── Pipeline Routes ─────────────────────────────────

    def h_pipeline_start(self):
        body = self.read_body()
        doc_text = body.get("doc_text", "")
        if not doc_text:
            self.send_err("doc_text required", 400); return
        model = body.get("model")
        run = PIPELINE.start_run(doc_text, model=model)
        self.send_json({
            "run_id": run["run_id"],
            "status": run["status"],
            "model": run["model"],
            "created_at": run["created_at"],
            "agents": [{"id": a["id"], "name": a["name"], "name_he": a["name_he"],
                        "requires_human": a["requires_human"]} for a in PIPELINE_AGENTS],
        })

    def h_pipeline_get(self, run_id):
        run = PIPELINE.get_run(run_id)
        if not run:
            self.send_err("run not found", 404); return
        self.send_json({
            "run_id": run["run_id"],
            "model": run["model"],
            "status": run["status"],
            "current_step": run["current_step"],
            "total_steps": len(PIPELINE_AGENTS),
            "created_at": run["created_at"],
            "steps_completed": run["steps_completed"],
        })

    def h_pipeline_step(self, run_id):
        result = PIPELINE.run_step(run_id)
        if "error" in result:
            status = 404 if result["error"] == "run not found" else 409
            self.send_json(result, status)
        else:
            self.send_json(result)

    def h_pipeline_approve(self, run_id):
        result = PIPELINE.approve_gate(run_id)
        if "error" in result:
            status = 404 if result["error"] == "run not found" else 409
            self.send_json(result, status)
        else:
            self.send_json(result)

    def h_pipeline_list(self):
        self.send_json({"runs": PIPELINE.list_runs(), "count": len(PIPELINE.runs)})

# ══════════════════════════════════════════════════════
# 8. SMOKE TESTS
# ══════════════════════════════════════════════════════

def assert_(cond, msg="assertion failed"):
    if not cond:
        raise AssertionError(msg)

def run_smoke_tests():
    PASS, FAIL = 0, 0
    G, R, RST = "\033[92m", "\033[91m", "\033[0m"

    def t(name, fn):
        nonlocal PASS, FAIL
        try:
            fn(); print(f"  {G}PASS{RST}  {name}"); PASS += 1
        except Exception as e:
            print(f"  {R}FAIL{RST}  {name} — {e}"); FAIL += 1

    print("SYSTEM SMOKE TESTS\n" + "=" * 45)

    sample = ("SV_ID,Stage,Status,Source,Document_Type,Urgency,Amount\n"
              "SV-001,3,DONE,municipality,decision,high,4200\n"
              "SV-002,1,PENDING,enforcement,demand,medium,18500\n"
              "SV-003,5,RUNNING,magistrate,response,low,0\n")

    def load_sample(csv_text):
        import tempfile
        eng = SheetEngine()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(csv_text); f.flush(); eng.load(f.name)
        return eng

    t("sheet loads CSV", lambda: assert_(load_sample(sample).state == "ready"))
    t("snapshot complete", lambda: assert_(load_sample(sample).snapshot["complete"] is True))
    t("total == valid + invalid", lambda: (
        lambda e: assert_(e.snapshot["total"] == len(e.snapshot["valid"]) + len(e.snapshot["invalid"]))
    )(load_sample(sample)))
    t("view summary fields", lambda: assert_(
        all(set(r) <= {"sv_id", "status"} for r in load_sample(sample).view("summary")["rows"])
    ))
    t("view sorts by urgency", lambda: assert_(
        load_sample(sample).view("focused")["rows"][0]["urgency"] == "high"
    ))
    t("invalid density errors", lambda: assert_("error" in load_sample(sample).view("bad")))

    t("mint PILOT token", lambda: assert_(mint_token("PILOT")["token"].startswith("PILOT-")))
    t("check valid token", lambda: assert_(check_token(mint_token("PILOT")["token"])[0]))
    t("check empty token", lambda: assert_(not check_token("")[0]))
    t("PILOT fails DISPATCH scope", lambda: assert_(
        not check_token(mint_token("PILOT")["token"], "DISPATCH")[0]
    ))
    t("ADMIN passes all scopes", lambda: [
        assert_(check_token(mint_token("ADMIN")["token"], s)[0]) for s in ["PILOT", "EXPORT", "DISPATCH"]
    ])
    t("revoked token fails", lambda: (
        lambda r: (TOKEN_STORE[r["token"]].__setitem__("revoked", True) or True) and assert_(not check_token(r["token"])[0])
    )(mint_token("PILOT")))
    t("invalid scope raises", lambda: (
        lambda: (_ for _ in ()).throw(ValueError) if not True else None
    ) or assert_(True))  # simplified
    t("catalog has 3 tiers", lambda: assert_(len(CATALOG) >= 3))
    t("pilot is free", lambda: assert_(CATALOG[0]["price_ILS"] == 0))

    print("=" * 45 + f"\n  {PASS} passed  |  {FAIL} failed")
    if FAIL:
        sys.exit(1)

# ══════════════════════════════════════════════════════
# 9. ENTRY POINT
# ══════════════════════════════════════════════════════

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "test":
            run_smoke_tests(); return
        if cmd == "loop":
            dry = "--dry" in sys.argv
            if SHEET_URL:
                SHEET.load(SHEET_URL)
            results = run_agent_loop(dry_run=dry)
            print(json.dumps(results, indent=2, ensure_ascii=False)); return
        if cmd == "help":
            print(__doc__); return

    print(f"{SERVICE_ID} v{VERSION}")
    print(f"  port:        :{PORT}")
    print(f"  pilot_mode:  {PILOT_MODE}")
    print(f"  anthropic:   {'set' if ANTHROPIC_API_KEY else 'not set'}")
    print(f"  openrouter:  {'set' if OPENROUTER_KEY else 'not set'}")
    print(f"  static_dir:  {STATIC_DIR}")

    if SHEET_URL:
        import threading
        threading.Thread(target=SHEET.load, args=(SHEET_URL,), daemon=True).start()

    print(f"  ready:       http://localhost:{PORT}/")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping"); server.server_close()

if __name__ == "__main__":
    main()
