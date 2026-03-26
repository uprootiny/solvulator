# Solvulator

12-agent legal document processing pipeline for Israeli law. Self-represented litigants.

## Stack
- Python 3 backend (system.py) — no framework, stdlib only
- 12-agent pipeline via OpenRouter/Gemini/Anthropic LLM APIs
- Single HTML UI (static/index.html)
- Keys in ~/.env.openrouter (OPENROUTER_API_KEY, GEMINI_API_KEY)

## Run
```
python3 src/system.py          # backend on :9800
python3 src/system.py test     # smoke tests
python3 agents/pipeline.py process test/sample.csv  # run pipeline
```

## Layout
```
src/system.py          — unified backend (HTTP server, sheet engine, token auth, LLM proxy)
agents/pipeline.py     — 12-agent pipeline runner (OpenRouter/Gemini)
agents/*.md            — agent system prompts (Hebrew)
static/index.html      — solvulator UI (v0.4)
static/projects.html   — project gallery
pipeline.edn           — pipeline spec (EDN)
test/sample.csv        — test data
```

## Ports
- :9800 — solvulator backend (system.py)

## Key invariants
- No API key in browser
- No agent skipped except 02 (born-digital)
- No dispatch without agent 10 approval
- No FOI auto-send
- Hebrew-first prompts, structured JSON output
- All estimates state assumptions and confidence
