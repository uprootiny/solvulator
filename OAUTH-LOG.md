# Google OAuth & Platform Integration Log

Track every attempt, failure mode, and lesson learned.

## Constraints We're Operating Under

- **Server: 173.212.203.211** — bare IP, no domain, no HTTPS
- **Google OAuth requires**: authorized redirect URIs, typically HTTPS for production
- **GIS (Google Identity Services)**: newer flow, works with localhost but unclear on bare IPs
- **Google Sheets API v4**: needs OAuth2 token with `spreadsheets.readonly` scope
- **Gemini API**: separate key (AIza...), not OAuth — works fine via REST
- **Free tier Gemini**: quota exhausted as of 2026-03-26 (429 rate limit, 0 requests remaining)

## Attempt Log

### ATT-01: GIS Token Client on bare IP (2026-03-26)
- **What**: storyboard.html loads `accounts.google.com/gsi/client`, creates TokenClient
- **Config**: client_id from localStorage, redirect to `http://173.212.203.211:9801`
- **Status**: UNTESTED — code written, no client_id configured yet
- **Risk**: Google may reject bare IP as redirect URI. GIS popup might block.
- **Notes**: legalcasework (serve.bb on :8340) has working GIS code — uses same pattern

### ATT-02: Gemini API direct (2026-03-26)
- **What**: `call_gemini()` in system.py using `GEMINI_API_KEY` (AIza...)
- **Result**: FAILED — 429 quota exhausted on free tier
- **Error**: `GenerateRequestsPerDayPerProjectPerModel-FreeTier` limit: 0
- **Lesson**: Free tier Gemini is unreliable for pipeline workloads (12 agents × N docs)
- **Mitigation**: OpenRouter fallback works ($0.000135/call via claude-sonnet-4)

### ATT-03: OpenRouter as Gemini proxy (2026-03-26)
- **What**: `call_claude()` cascade falls through to OpenRouter with `anthropic/claude-sonnet-4`
- **Result**: SUCCESS — verified with pipeline step 1 and 2
- **Cost**: ~$0.00014/call, acceptable for pilot
- **Note**: Could route to `google/gemini-2.5-flash` via OpenRouter to avoid free tier limits

### ATT-04: Google Sheets CSV export URL (prior sessions)
- **What**: SheetEngine fetches published CSV from Google Sheets URL
- **Config**: `SHEET_URL` env var or POST /sheet/reload with source
- **Status**: Code works but SHEET_URL never configured
- **Blocker**: Need the actual published CSV URL from the Google Sheet
- **Note**: Sheet must be "Published to web" (File → Share → Publish) as CSV

### ATT-05: Google Sheets API v4 via OAuth (legalcasework)
- **What**: ClojureScript `google.cljs` in legalcasework does full OAuth + Sheets API
- **Hardcoded sheet**: `1oCy3bt0UfwPNrvql0XI2Pkh6e9xGnar5kaA2IzYRD2w`
- **Scopes**: sheets, drive.readonly, gmail.compose, gmail.send
- **Status**: WORKS in legalcasework on :8340
- **Issue**: Client ID stored in localStorage, manual paste workflow
- **Lesson**: This pattern works — copy it to solvulator storyboard

### ATT-06: ~/.env.openrouter key loading (2026-03-26)
- **What**: system.py reads API keys from `~/.env.openrouter` at startup
- **Keys found**: OPENROUTER_API_KEY (set), GEMINI_API_KEY (set), ANTHROPIC_API_KEY (not in file)
- **Result**: Partial success — OpenRouter works, Gemini quota-blocked, Anthropic unavailable
- **Lesson**: Need all three keys in the file for full fallback chain

---

## Known Google Platform Gotchas

1. **Bare IP + OAuth**: Google Cloud Console may not accept `http://173.212.203.211:PORT` as authorized origin. Workaround: use `localhost` during dev, or set up a domain.

2. **GIS vs Legacy OAuth**: GIS (google.accounts.oauth2.initTokenClient) is the current approach. Legacy (gapi.auth2) is deprecated. Don't mix them.

3. **Popup blockers**: GIS token flow opens a popup. On iOS Safari, this can be blocked if not triggered by user gesture. Must be called from onclick handler.

4. **Token expiry**: GIS access tokens expire in 1 hour. Need refresh logic. legalcasework has this (1-min early refresh).

5. **Published CSV vs API**: CSV export is read-only, no auth needed, but schema changes require manual republishing. API v4 is live but needs OAuth.

6. **Gemini free tier**: 0 requests remaining as of today. Either upgrade to paid, or route Gemini calls through OpenRouter (`google/gemini-2.5-flash` model).

7. **CORS on Sheets API**: Direct browser→Sheets API works with OAuth token. No CORS issues. But CSV export URL may have CORS restrictions depending on Google's mood.

---

## Decision Matrix

| Approach | Auth Needed | CORS | Reliability | Cost |
|----------|-------------|------|-------------|------|
| CSV export URL | None | Maybe | Low (manual publish) | Free |
| Sheets API v4 + OAuth | Yes (GIS) | OK | High | Free |
| OpenRouter → Gemini | API key | N/A (server) | High | ~$0.0001/call |
| Direct Gemini API | API key | N/A (server) | Low (quota) | Free (exhausted) |
| Anthropic direct | API key | N/A (server) | High | ~$0.003/call |

### ATT-07: GIS OAuth in storyboard.html (2026-03-26)
- **What**: Ported GIS token client pattern from legalcasework into storyboard.html
- **Flow**: Settings panel → paste Client ID → Connect → OAuth popup → token stored in localStorage
- **Scope**: `spreadsheets.readonly`
- **Fallback chain**: Backend /sheet/view → Direct Sheets API v4 with Bearer token → SAMPLE_CASES
- **Status**: CODE WRITTEN — needs testing with actual Client ID
- **Risk**: Bare IP redirect URI may be rejected by Google. iOS Safari popup may be blocked.
- **Token handling**: Stored in localStorage as `sv-gtoken`, cleared on 401

### ATT-08: Gemini-first pipeline with sheet knowledge base (2026-03-26)
- **What**: Pipeline agents now prefer call_gemini() over call_claude(), inject sheet data as context
- **Knowledge base**: Auto-populated from SHEET snapshot (sv_ids, statuses, urgencies)
- **Fallback**: If Gemini fails (429 etc), falls through to call_claude() cascade
- **Status**: CODE WRITTEN — will hit Gemini quota on first try, then fall to OpenRouter
- **Lesson**: Need paid Gemini tier or route through OpenRouter's google/gemini-2.5-flash

### ATT-09: Google Apps Script pipeline (2026-03-26, from user)
- **What**: Full server-side Google automation: Gmail scan → Drive OCR → Gemini analysis → Sheet write
- **Components**: GmailApp, DriveApp, SpreadsheetApp, Gemini 2.5 Flash
- **Sheet ID**: `1cK4F7_5gGB_inEhZieiZdrdVbZOZWqE2phpHEO2WStM` (THIS is the live sheet)
- **Drive folder**: `1FG27TPmB0LcVl-c7hBcZiFC3aqVk2u8b`
- **OCR**: Drive API insert with `{ocr:true}` → temp Google Doc → extract text → delete
- **Analysis output schema**: case_number, case_name, plaintiff, defendant, lawyers, type, status, decision, deadlines, strategies, exhibits[], facts[]
- **Status**: WORKING (runs in Google Apps Script, no OAuth needed — runs as user)
- **Key insight**: This is the UPSTREAM of solvulator. Documents enter via email, get OCR'd and analyzed, land in Sheets. Solvulator reads from Sheets.
- **Schema mismatch**: Apps Script writes 14+ dynamic columns. Solvulator SheetEngine expects 7 fixed columns (SV_ID, Stage, Status, Source, Document_Type, Urgency, Amount). NEED TO RECONCILE.

### ATT-10: Direct CSV export from Sheet 1cK4F7 (2026-03-26)
- **What**: Tried loading `https://docs.google.com/spreadsheets/d/1cK4F7.../export?format=csv`
- **Result**: FAILED — HTTP 401 Unauthorized
- **Cause**: Sheet not published to web. Google Sheets CSV export requires either:
  a. Sheet is "Published to web" (File → Share → Publish), OR
  b. OAuth token with Sheets API scope
- **Fix needed**: Either publish the sheet, or use Sheets API v4 with OAuth

### ATT-11: Try legalcasework sheet (2026-03-26)
- **What**: Try the other known sheet `1oCy3bt0UfwPNrvql0XI2Pkh6e9xGnar5kaA2IzYRD2w`
- **Status**: TESTING

## Next Attempts to Try

- [ ] ATT-09: Actually test GIS OAuth popup on iPhone Safari with bare IP
- [ ] ATT-10: Try OpenRouter's `google/gemini-2.5-flash` model to bypass free tier
- [ ] ATT-11: Set up actual domain (solvulator.com → 173.212.203.211) for proper OAuth
- [ ] ATT-12: Test sheet write-back (update rows with pipeline results)
