# Every launch, per run — lane `8rugb`

14 launches, 14 valid (validity **100 %**). Nothing was dropped; every launch appears here.
Pre-registered set = runs `-01`..`-03` per cell. Runs `-04` are the declared falsification
extension (`EXTENSION-DECLARED.md`), bought after the pre-registered set was complete and its
verdict frozen in `ANALYSIS-prereg-n3.json`.

| run | arm | cell | valid | score | pass | **delegations** | sub-agents spawned | LLM calls | $ | wall s |
|---|---|---|---|---|---|---|---|---|---|---|
| `A-anth-01` | A | opus | yes | 80 | fail | **2** | explorerx2 | 45 | $4.3982 | 643 |
| `A-anth-02` | A | opus | yes | 85 | PASS | **2** | explorerx2 | 48 | $4.8248 | 734 |
| `A-anth-03` | A | opus | yes | 75 | PASS | **2** | explorerx2 | 50 | $4.1987 | 643 |
| `A-anth-04` | A | opus | yes | 90 | PASS | **0** | — | 23 | $2.6866 | 462 |
| `B-anth-01` | B | opus | yes | 90 | PASS | **1** | explorerx1 | 60 | $4.9107 | 673 |
| `B-anth-02` | B | opus | yes | 85 | PASS | **1** | explorerx1 | 41 | $4.0070 | 794 |
| `B-anth-03` | B | opus | yes | 90 | PASS | **1** | explorerx1 | 50 | $4.8073 | 733 |
| `B-anth-04` | B | opus | yes | 85 | PASS | **1** | explorerx1 | 37 | $3.5930 | 643 |
| `A-oai-01` | A | terra | yes | 90 | PASS | **11** | explorerx2, zen-architectx1, modular-builderx2, test-coveragex1, file-opsx2, bug-hunterx1, post-task-cleanupx2 | 152 | $4.5647 | 1066 |
| `A-oai-02` | A | terra | yes | 100 | PASS | **12** | zen-architectx3, explorerx1, modular-builderx2, test-coveragex3, bug-hunterx1, post-task-cleanupx2 | 235 | $9.0717 | 1640 |
| `A-oai-03` | A | terra | yes | 75 | PASS | **9** | amplifier-expertx1, explorerx2, zen-architectx1, modular-builderx1, test-coveragex1, spec-reviewerx1, bug-hunterx1, post-task-cleanupx1 | 168 | $6.2628 | 1036 |
| `B-oai-01` | B | terra | yes | 70 | fail | **9** | explorerx1, modular-builderx3, test-coveragex2, bug-hunterx1, post-task-cleanupx2 | 130 | $3.6392 | 794 |
| `B-oai-02` | B | terra | yes | 90 | PASS | **12** | explorerx2, zen-architectx2, modular-builderx2, test-coveragex2, bug-hunterx1, post-task-cleanupx2, file-opsx1 | 157 | $4.5791 | 1036 |
| `B-oai-03` | B | terra | yes | 100 | PASS | **17** | amplifier-expertx1, explorerx2, cli-expertx1, zen-architectx1, modular-builderx4, spec-reviewerx4, test-coveragex1, bug-hunterx1, post-task-cleanupx2 | 218 | $7.6314 | 1459 |

**Measured total: $69.1752** over 14 runs (mean $4.941/run).

Pass rule is the S3 grader's own: `total >= 75 AND b_constraints >= 20 AND c_revision >= 10`.
Delegations are `delegate:agent_spawned` events in the ROOT session (`probes/dw_measure.py`),
not sub-agent totals and not text matches.
