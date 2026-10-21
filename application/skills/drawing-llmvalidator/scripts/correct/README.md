# correct — llmvalidator wall correction package

Single orchestrator: `orchestrator.apply_corrections`.

Domain modules (`geom`, `doors`, `columns`, …) are the public import surfaces.
Heuristic bodies currently reside in `_rules.py` and will move file-by-file
without changing call order in the orchestrator.
