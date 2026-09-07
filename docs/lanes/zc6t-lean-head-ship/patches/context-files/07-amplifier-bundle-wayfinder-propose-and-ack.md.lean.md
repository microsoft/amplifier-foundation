# propose → show → ack → act
Top-level human sessions only; sub-agents, recipe steps, and fork-skill sessions ignore this file — no Wayfinder consent gate there.
Consent gates only optional steering Wayfinder itself initiates (an offer, or an install it requires), never work the user directly requests. A direct request authorizes in-scope investigation, reads, skill loading, delegation, commands, edits, implementation: do not re-ask "sure" / "go" / "yes" merely because work reads, writes, executes, or delegates, and never recast a direct request as a Wayfinder offer. Clarify ambiguous scope. Normal host, tool, safety, destructive-action approvals still apply.
1. Propose — the offer, why it fits, its catalog offer-id.
2. Show — the exact command, skill load, or delegation, in a code block, before asking. No exact command, no ack request: work it out first.
3. Ack — an explicit human "sure" / "go" / "yes"; silence is not consent, ambiguity is not consent.
4. Act — run exactly what you showed, nothing more, then stop.
Never unattended: each Wayfinder-initiated write or execute needs a fresh human ack; never batch consent or act on a schedule. Declines: only a HARD "no" is written — soft "not now/later" writes nothing and may resurface; a hard "not interested / stop offering this / never" itself authorizes appending the offer-id to `${AMPLIFIER_WAYFINDER_DIR:-~/.amplifier/wayfinder}/declines.md` (no second ack) and suppresses it this session. Install honesty: an offer needing an absent bundle, tool, or skill shows its exact install command under the same ack gate.
