# Knowledge protocol (Akela)

You (the agent) run this loop on every task. The compiled slice is your only source of domain
knowledge — it is the current, evidence-vetted view; do not read the wiki directly.

Before starting a task:
1. `akela compile --activity <activity> --task <task-id>` — prints the slice path.
2. Read that slice. Every rule in it has a source id (`WIKI-…#…`, `LRN-…`, `EXT-…`).

While working: note which rules you actually relied on, and any rule the outcome proved wrong.

After finishing:
3. `akela log applied <source-id>` — once per rule you relied on.
4. `akela log contradicted <source-id> --note "<what happened — quote the disputed text verbatim>"`
   — for any rule the outcome contradicted. Verbatim quotes make blame verifiable; paraphrase gets rejected.
5. If the environment gave you a verdict (review, CI, rejection), it applies to a specific rule
   you relied on — contradict that rule, do not just note the failure.
6. Learned something the slice did not contain? Check it first: `akela vet` (stdin: JSON array of
   candidate statements). Only statements it accepts are worth proposing as learnings.
7. `akela log outcome --status DONE` (or BLOCKED / NEEDS_CONTEXT) — closes the run.

If the compile summary reports unscoped sections and the count is new or has grown, the wiki
gained pages agents cannot see — mention it to the owner and offer to draft the scoping
(akela/ONBOARD.md). Do not treat it as an error; unscoped is a pending decision, not a failure.

Escalate, don't sit on it: if what you just logged is critical — you contradicted a must-tier
rule, closed BLOCKED or NEEDS_CONTEXT because knowledge was missing or wrong, or the slice
contained rules that contradict each other — tell the owner what you found and ask whether they
want the curation review (akela/CURATE.md) run now instead of waiting for the scheduled one.

Never edit the wiki, LEARNINGS.md, or the log files directly. You report; the counts recommend;
the curator decides.
