# Curation protocol (Akela) — the review routine

You (the agent) are running a curation review for the owner. This is a RECURRING routine —
weekly or per sprint by default; with heavy daily task volume, more often is fine (an empty
review costs a minute), and run it early when contradictions or unscoped sections accumulate; if your harness supports scheduled or recurring tasks,
offer to set it up on that cadence, and at the end of each review remind the owner when the
next one is due. You draft; the owner approves.
Apply no edit without approval. The counts are honest, but honesty is not truth — your job is
to bring the owner evidence and a recommendation, not to act on arithmetic alone.

1. Run `akela stats`. Build the review queue from the finding column, in this order:
   falsified → unscoped → promotion candidates → restates → dormant (never applied).
2. For every FALSIFIED source, read its contradiction notes in the log before recommending.
   Distinguish: (a) the rule is genuinely stale → recommend rewrite or retirement;
   (b) the workers are wrong — common right after the owner has updated a rule (a fresh
   rewrite has few applied to defend itself) → recommend leaving it, and say why.
3. For every PROMOTION CANDIDATE, judge the statement itself: a concrete fact that earned its
   record → recommend promoting (write the section with from=<id> lineage, mark the learning
   promoted; for EXT- notes, also retire what they supersede). A vague hedge that free-rode
   being packed → recommend leaving or retiring it. The bar measures use, not truth.
4. For UNSCOPED sections: recommend a scope/tier if agents need it, or explicitly leave it out.
   For RESTATES: recommend retiring the learning or merging its delta into the section.
   For DORMANT: recommend narrower scope, lower tier, or retirement — it pays rent in every slice.
5. Present ONE review table — source · finding · recommendation · one-line reason — and wait.
6. Apply only what the owner approves, then run `akela check`. If check refuses, fix the cause
   or revert that edit; never leave the knowledge base failing check.
7. Close with a summary: edits applied, recommendations declined, and anything deferred.
