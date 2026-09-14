# Onboarding protocol (Akela) — bringing knowledge into scope

You (the agent) are drafting knowledge scoping for the owner to review — either the initial
pass over a fresh install, or a later pass over whatever `akela stats` marks `unscoped`
(new pages accumulate; this protocol is how they get onboarded too).
You propose; the owner decides. Do not edit any wiki file or config without their approval.

1. Read `akela.json`. If `activities` is empty or missing, first study the project (its docs,
   its recurring kinds of work) and propose 3–7 activity names — the units of work agents will
   run here. Wait for approval before continuing.
2. Run `akela index --json` and read the wiki files it lists — or, on a re-run, only the
   sections `akela stats` marks `unscoped`. For each section, judge:
   - which activities actually need it → proposed `scope`
   - how critical it is → proposed `tier`: `must` only for rules an agent must never work
     without (keep this floor SMALL — every must packs on every matching task); `should` for
     the useful majority; `context` for background.
   - not knowledge an agent needs (changelogs, meeting notes, marketing) → leave unscoped.
3. Present the proposal as a review table — section id · proposed scope · proposed tier · one-line
   reason — plus, for the approved outcome, BOTH forms and let the owner pick:
   - config-only: a `compiler.scope` block for `akela.json` (no wiki edits), or
   - tags: the exact `<!-- akela: id=… scope=… tier=… -->` lines to insert per section.
4. Aim small: a working start is ~10 well-scoped sections per activity, not complete coverage.
   Anything missed will surface later as evidence (unscoped findings, NEEDS_CONTEXT outcomes) —
   growing scope on demand is the designed path.
5. On a LARGE wiki, do not read everything. Triage from the index first (`akela index` lists
   every section id and heading without the bodies); shortlist by heading; read only shortlisted
   files. Prefer one file-level tag (after the # H1) over per-section tags when a whole file
   serves one purpose. Work one activity at a time and deliver each as its own small review —
   the owner's review time, not your reading time, is the scarce resource.
6. After approval is applied, run `akela index` and `akela compile --activity <one> --task TEST-1`
   and show the owner the first slice — then this protocol is done; daily work follows PROTOCOL.md.
