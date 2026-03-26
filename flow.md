# Legal Document Processing Pipeline -- Flow Diagram

## Full Pipeline Flow

```mermaid
flowchart TD
    subgraph INTAKE["Phase 1: INTAKE"]
        DOC[/"Document Received"/]
        A01["01 Intake & Classification<br/>classify | urgency | deadlines"]
        G01{{"HUMAN GATE<br/>Approve Classification"}}
        A02["02 OCR & Text Integrity<br/>extract | confidence | structure"]
        G02{{"HUMAN GATE<br/>Verify Text"}}
        SKIP02(("Skip if<br/>born-digital"))
    end

    subgraph ANALYSIS["Phase 2: ANALYSIS"]
        A03["03 Operative Extraction<br/>deadlines | obligations | hearings | sanctions"]
        G03{{"HUMAN GATE<br/>Confirm All Components"}}
        A04["04 Legal Structure Mapper<br/>claims | proportionality | laches"]
        A05["05 Weakness Detector<br/>missing procedure | contradictions | gaps"]
    end

    subgraph INTELLIGENCE["Phase 3: INTELLIGENCE"]
        A06["06 FOI Trigger Analyzer<br/>policies | procedures | statistics"]
        G06{{"HUMAN GATE<br/>Review FOI Drafts"}}
        A07["07 Damage Exposure Calculator<br/>property | access | income | legal costs"]
    end

    subgraph ACTION["Phase 4: ACTION"]
        A08["08 Strategy Builder<br/>alternatives | risk-benefit | timeline"]
        HUMAN_SELECT(("Human Selects<br/>Strategy"))
        A09["09 Draft Generator<br/>6-section structure | Hebrew draft"]
    end

    subgraph CONTROL["Phase 5: CONTROL"]
        A10["10 Human Verification Gate<br/>5-point checklist"]
        G10{{"HUMAN GATE<br/>Approve for Dispatch"}}
        A11["11 Filing & Dispatch Controller<br/>file | serve | record | remind"]
    end

    subgraph LEARNING["Phase 6: LEARNING"]
        A12["12 Meta-Pattern Recorder<br/>barriers | frequencies | outcomes | trends"]
        DB[("Pattern<br/>Database")]
    end

    %% Intake flow
    DOC --> A01
    A01 --> G01
    G01 -->|Approved| A02
    G01 -->|Rejected| A01
    A01 -.->|Digital text| SKIP02
    SKIP02 -.-> A03
    A02 --> G02
    G02 -->|Verified| A03
    G02 -->|Corrections needed| A02

    %% Analysis flow
    A03 --> G03
    G03 -->|Confirmed| A04
    G03 -->|Missing items| A03
    A04 --> A05

    %% Analysis to Intelligence (parallel fan-out)
    A05 --> A06
    A05 --> A07

    %% FOI side channel
    A06 --> G06
    G06 -->|"Approved drafts sent independently"| FOI_OUT[/"FOI Requests<br/>(side channel)"/]

    %% Intelligence to Action (join)
    A06 --> A08
    A07 --> A08

    %% Action flow
    A08 --> HUMAN_SELECT
    HUMAN_SELECT --> A09

    %% Control flow
    A09 --> A10
    A10 --> G10
    G10 -->|Approved| A11
    G10 -->|"Blocking issues"| A09

    %% Learning
    A11 --> A12
    A12 --> DB
    A12 --> DONE[/"Pipeline Complete"/]

    %% Styling
    style INTAKE fill:#4a90d9,color:#fff,stroke:#2c5f8a
    style ANALYSIS fill:#e6a23c,color:#fff,stroke:#b8821f
    style INTELLIGENCE fill:#9b59b6,color:#fff,stroke:#7d3c98
    style ACTION fill:#27ae60,color:#fff,stroke:#1e8449
    style CONTROL fill:#e74c3c,color:#fff,stroke:#c0392b
    style LEARNING fill:#607d8b,color:#fff,stroke:#455a64

    style G01 fill:#ff6b6b,color:#fff,stroke:#c0392b
    style G02 fill:#ff6b6b,color:#fff,stroke:#c0392b
    style G03 fill:#ff6b6b,color:#fff,stroke:#c0392b
    style G06 fill:#ff6b6b,color:#fff,stroke:#c0392b
    style G10 fill:#ff6b6b,color:#fff,stroke:#c0392b
    style HUMAN_SELECT fill:#ff9f43,color:#fff,stroke:#e67e22

    style DB fill:#34495e,color:#fff,stroke:#2c3e50
    style DOC fill:#2c3e50,color:#fff
    style DONE fill:#2c3e50,color:#fff
    style FOI_OUT fill:#8e44ad,color:#fff,stroke:#6c3483
    style SKIP02 fill:#95a5a6,color:#fff
```

## Data Flow Between Agents

```mermaid
flowchart LR
    S01["01: classification<br/>deadlines<br/>routing"]
    S02["02: full_text<br/>structure<br/>citations"]
    S03["03: operative_components<br/>completion_requests<br/>deadline_conflicts"]
    S04["04: claims<br/>property_rights<br/>laches_analysis"]
    S05["05: weaknesses<br/>contradictions<br/>severity_summary"]
    S06["06: foi_triggers<br/>draft_requests"]
    S07["07: damage_exposure<br/>tort_claims<br/>cost_estimate"]
    S08["08: alternatives<br/>risk_benefit<br/>do_nothing"]
    S09["09: hebrew_draft<br/>6_sections<br/>style_checks"]
    S10["10: 5_point_check<br/>blocking_issues<br/>recommendation"]
    S11["11: filing_record<br/>service_proof<br/>reminders"]
    S12["12: patterns<br/>trends<br/>insights"]

    S01 --> S02
    S02 --> S03
    S03 --> S04
    S04 --> S05
    S05 --> S06
    S05 --> S07
    S06 --> S08
    S07 --> S08
    S08 --> S09
    S09 --> S10
    S10 --> S11
    S11 --> S12
```

## Agent Dependency Matrix

Each agent receives cumulative context from prior stages. The matrix below shows which prior stage outputs each agent consumes directly.

```
Agent  | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 | 11
-------|----|----|----|----|----|----|----|----|----|----|----
01     |    |    |    |    |    |    |    |    |    |    |
02     | x  |    |    |    |    |    |    |    |    |    |
03     | x  | x  |    |    |    |    |    |    |    |    |
04     | x  | x  | x  |    |    |    |    |    |    |    |
05     | x  | x  | x  | x  |    |    |    |    |    |    |
06     | x  | x  |    | x  | x  |    |    |    |    |    |
07     | x  |    | x  | x  | x  |    |    |    |    |    |
08     | x  |    | x  | x  | x  | x  | x  |    |    |    |
09     | x  | x  | x  | x  | x  |    | x  | x  |    |    |
10     | x  | x  | x  | x  | x  |    | x  | x  | x  |    |
11     | x  |    | x  |    |    |    |    |    | x  | x  |
12     | x  |    | x  | x  | x  |    | x  | x  |    |    | x
```

## Human Gates Summary

| Gate | Agent | Action Required | On Rejection | Blocking |
|------|-------|-----------------|--------------|----------|
| G01 | 01 - Intake | Approve classification, urgency, deadlines | Re-classify | Yes |
| G02 | 02 - OCR | Verify text accuracy, correct uncertain regions | Re-OCR | Yes |
| G03 | 03 - Operative | Confirm all components captured, add missed items | Re-extract | Yes |
| G06 | 06 - FOI | Review each FOI draft, approve/modify/reject individually | Revise drafts | No (side channel) |
| -- | 08 - Strategy | Human selects strategy from alternatives | N/A | Yes (selection required) |
| G10 | 10 - Verification | 5-point check: facts, law, dates, consistency, risk | Revise draft (back to 09) | Yes |

## Key Invariants

1. **No dispatch without human approval**: Agent 11 will not execute without Agent 10 `human_approved: true`.
2. **No FOI auto-send**: Agent 06 drafts only. Human decides which, if any, to send.
3. **No aggressive language**: Agent 09 runs style checks. Failures require revision.
4. **No personal attribution**: All critique targets decisions, not decision-makers.
5. **No pattern data on individuals**: Agent 12 records institutional patterns only.
6. **All estimates state assumptions**: Agent 07 damage figures include confidence levels and assumption lists.
7. **Append-only pattern store**: Agent 12 never deletes historical data.
