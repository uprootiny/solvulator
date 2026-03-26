# Solvulator Roadmap

## Phase 1: BACKBONE (done)
- [x] Consolidate repo to ~/solvulator
- [x] system.py unified backend on :9801
- [x] Pipeline orchestration integrated (start/step/approve/status)
- [x] LLM proxy working (OpenRouter confirmed)
- [x] Smoke tests 15/15
- [x] ARCHITECTURE.md, POSTMORTEM.md
- [x] Push to uprootiny/solvulator

## Phase 2: STORYBOARD UI (done)
- [x] iPhone-first PWA storyboard (storyboard.html)
- [x] 5 zoom levels: Weave → Timeline → Phase → Context → Deep
- [x] Pinch-to-zoom gesture handling
- [x] Share target for iOS
- [x] Service worker for offline
- [x] manifest.json with homescreen install
- [x] VS Code Live Server config for preview

## Phase 3: SHEETS + GEMINI INTEGRATION (current)
- [ ] Google Sheets OAuth flow in storyboard UI
- [ ] Sheet data → pipeline knowledge base
- [ ] Pipeline agents run over Gemini (not just OpenRouter)
- [ ] Sheet rows enriched with agent outputs (write-back)
- [ ] Storyboard loads live data from sheets via backend

## Phase 4: STORYBOARD POLISH
- [ ] Smooth zoom transitions (CSS transforms, not re-renders)
- [ ] Between-level interpolation (L1.5, L2.5 etc)
- [ ] Document text with selectable highlights
- [ ] Margin annotations that persist
- [ ] "Gather notes → process → shaped document" flow
- [ ] Timeline cross-linking (event → doc → implications)
- [ ] Dependency graph visualization

## Phase 5: FLEET SEPARATION
- [ ] Split metaops/grafana monitoring into own repo
- [ ] Split myclaizer into standalone publishable PWA
- [ ] Split legal-warroom into standalone repo
- [ ] Each gets own CLAUDE.md, CI, README

## Phase 6: PRODUCTION
- [ ] SQLite persistence for pipeline runs
- [ ] Proper error handling + retry in pipeline
- [ ] Gemini quota management (paid tier or rotation)
- [ ] Domain: solvulator.com deployment
- [ ] HTTPS + auth for public access
