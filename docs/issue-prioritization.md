# How Omnigent prioritizes issues

We rank open community issues so maintainers see the most important work first.
The ranking is a triage aid, not a delivery promise or roadmap commitment.

## How the ranking works

An LLM reads the issue title, body, and labels and classifies its type, severity,
and affected areas. It does not assign the final priority directly. Priority
comes from deterministic arithmetic:

```text
score = severity points × component weight + community-demand points
```

| Signal | Current treatment |
| --- | --- |
| Severity | S0=100, S1=60, S2=30, S3=10. It captures impact and reach. |
| Component | The highest matching area weight, currently 0.9–1.4. |
| Community demand | GitHub `+1` reactions add up to 15 points, capped at 12 reactions. |
| Needs information | An issue labeled `needs-info` scores zero until the missing information arrives. |

Scores map to priority labels as follows:

| Priority | Score |
| --- | ---: |
| `P0-critical` | 100 or higher |
| `P1-high` | 60–99.99 |
| `P2-medium` | 25–59.99 |
| `P3-low` | Below 25 |

Age, readiness, and duplicate-count adjustments are not currently enabled.
Component importance is a separate signal, so severity is not raised merely
because an issue affects a particular harness or subsystem.

## Human judgment wins

Maintainers can correct severity, component, or priority labels when context is
missing from the model. Automation preserves those overrides and does not
replace a maintainer-set priority with its own proposal. The queue is rerun as
issues change, while unchanged LLM classifications are reused.

## Helping us triage accurately

For bugs, include the observed impact, reproduction steps, Omnigent version,
platform, and affected harness or authentication mode. For feature requests,
describe the user problem and expected reach. Use a `+1` reaction when an
existing issue matters to you; ordinary comments are not counted as votes.

The scoring configuration and component map are public in
[`default_scoring.json`](../.github/triage_v2/src/issue_prioritization/default_scoring.json)
and [`areas.json`](../.github/areas.json).
