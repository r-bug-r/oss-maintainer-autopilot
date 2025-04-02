# Architecture

## Shape

Autopilot is a single Python 3.12 process with four boundaries: webhook intake,
model generation, proposal approval, and forge writes. Only the last one can
change something outside the process, and it is reachable only through an
approved proposal.

```mermaid
flowchart LR
    GH[GitHub] -->|signed webhook| API[api.py<br/>verify + dedupe]
    API --> NORM[webhooks.py<br/>normalize]
    NORM --> STORE[(SQLite<br/>store.py)]
    NORM --> WF[workflows.py]
    WF --> DUP[duplicates.py<br/>deterministic candidates]
    WF --> GW[gateway.py<br/>budget + cache + ledger]
    GW --> PROV[providers.py<br/>fake | OpenAI-compatible]
    WF --> SCHEMA[schemas.py<br/>closed output schema]
    SCHEMA --> STORE
    CLI[cli.py] --> WF
    API --> WF
    STORE -->|approved proposal| EXEC[workflows.execute<br/>claim once]
    EXEC --> ADAPT[adapters.py<br/>local fixture | GitHub]
    ADAPT -->|comment, labels| GH
    STORE --> AUDIT[(hash-chained<br/>audit_events)]
```

