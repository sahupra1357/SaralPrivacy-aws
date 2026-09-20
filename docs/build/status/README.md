One file per module, owned by that module's builder: `<module>.md`.

```
# <module> — status: inventory | building | static-clean
Updated: <date>
## Files created
## Files changed (frontend call sites etc.)
## Moved to _backup (also in _backup/LEDGER.md)
## Needs from orchestrator
- include_router: <exact line>
- models import: <exact line>
- jobs: <name>, <cron>
- dependencies: backend [...] frontend [...]
## Open questions
```
