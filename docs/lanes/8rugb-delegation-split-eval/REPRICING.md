# Re-priced at OBSERVED prices, written BEFORE the bulk spend

Wave 1 = one launch per cell (4 launches), run first precisely to replace the previous lane's
*quoted* prices with *measured* ones. Those quoted prices ($3.53 opus-5, $4.63 terra) were observed
on **anchors-amp-dev**-root S3 runs; this eval is **foundation**-root, whose composed system prompt
is 123,056 chars (arm A) / 99,691 chars (arm B) — so the price had to be re-measured, not assumed.

## Observed, foundation-root S3, medium effort (measured `cost_usd`, dw_measure)

| cell | run | cost | wall | delegations |
|---|---|---|---|---|
| A · opus-5 | `A-anth-01` | **$4.3982** | 643 s | 2 |
| B · opus-5 | `B-anth-01` | **$4.9107** | 673 s | 1 |
| B · terra  | `B-oai-01`  | **$3.6392** | 794 s | 9 |

opus-5 observed mean **$4.65/run** vs $3.53 quoted → **1.32×**.
terra observed **$3.64/run** vs $4.63 quoted → **0.79×** (n=1 at this point).
Blended observed ≈ **$4.32/run**.

## The arithmetic, restated against the $80 authority

```
spent in wave 1 (3 measured runs so far)                        =  $12.95
remaining valid runs still required (12 - 3 valid)              =   9        [+A-oai-01 in flight]
at 100% validity:   9 launches x $4.32                          =  $38.88
at the program's observed 67% validity: 9/0.67 = 14 launches    =  $60.48
                                                       TOTAL    =  $73.43  (67% validity)
                                                       TOTAL    =  $51.83  (100% validity)
authority                                                       =  $80.00
```

**The arithmetic closes at the authority**, with $6.57 of headroom at the pessimistic 67 % validity
assumption. The design is therefore executed **as pre-registered — 2 arms x 2 providers x n>=3 valid** —
with no arm dropped, no provider dropped, and no n reduced.

Stop rule, unchanged from the goal: if the remaining budget cannot buy the **smallest indivisible
purchase that advances a deliverable** (one launch, ~$4.32), the residue is reported as unspendable
rather than described as remaining budget.
