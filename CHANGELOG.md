# SubInspector — Change Log

All significant changes to the SubInspector bot, grouped by feature area.
Most recent changes appear first within each section.

---

## Deployment & Infrastructure

| What | Detail |
|---|---|
| **Hosting** | HuggingFace Spaces (Docker, `uvicorn main:app --port 7860`) |
| **Git remotes** | Two remotes — `origin` (GitHub: ASHRITHAAKKINEPALLY/subinspector) and `hf` (HuggingFace Space). **Both must be pushed** on every deploy: `git push origin main && git push hf main` |
| **Keep-alive** | Self-ping to `/health` every 4 minutes to prevent HF Space from sleeping |
| **Webhook auto-heal** | On every startup, `ensure_webhook()` checks ClickUp for the registered webhook. If suspended or missing it deletes and recreates it automatically |
| **LLM** | Primary: `llama-3.3-70b-versatile` (Groq). Fallback: `llama-3.1-8b-instant` on rate limit. Both at `temperature=0` |

---

## Scope & Routing

### Which tickets get checked

| Layer | Variable | Default value | Behaviour |
|---|---|---|---|
| Enforcement (full gate + revert) | `ENFORCEMENT_FOLDERS` | `90165998786` (IH) | Set via HF secret |
| Enforcement (space-level) | `ENFORCEMENT_SPACES` | _(empty)_ | Optional override |
| Advisory (comment only, no revert) | `ADVISORY_FOLDERS` | 9 client folders (see below) | Hardcoded defaults |
| Advisory (space-level) | `ADVISORY_SPACES` | _(empty)_ | Optional override |

**Advisory client folders (hardcoded defaults):**
- HexClad: `90161200308`
- Saxx: `90161875051`
- BBoutique: `90169023555`, `90169104190`
- Naked & Thriving: `90167972037`
- Javvy Coffee: `90169078001`
- Yum Brands: `90164305799`
- Momentous Projects: `90160230070`
- BPN Consulting: `90020845754`
- BPN DE: `90160770330`

**3-level scope check (OR logic):** For each incoming webhook, the task's `folder.id`, `list.id`, and `space.id` are all checked. A task is in scope if any one of the three matches the enforcement or advisory sets. This ensures master/parent tickets (which may be in a different list than sprint tasks) are always caught.

### Advisory mode vs Enforcement mode

| | Enforcement (IH) | Advisory (external clients) |
|---|---|---|
| Gate checks | ✅ All 3 gates | ✅ All 3 gates |
| Status revert on FAIL | ✅ Yes | ❌ No |
| Escalation after 2 failures | ✅ Yes | ❌ No |
| Auto-complete on CLOSURE 5/6 | ✅ Yes | ❌ No |

---

## Gate Triggers

| Event | Gate triggered |
|---|---|
| `taskCreated` | INTAKE (always) |
| `taskStatusUpdated` → ready / in progress / development / code-review | PRE-EXECUTION |
| `taskStatusUpdated` → qa / uat / prod-review / complete / done / ready to close | CLOSURE |
| `taskCommentPosted` with `/si check` or `/subinspector check` | Whichever gate matches the current status; dry-run (no revert) |

**Status revert maps** — used when the webhook payload is missing `previous_status`:
- PRE-EXECUTION FAIL → reverts to `open`
- CLOSURE FAIL → reverts to `prod-review` (only for complete / done / ready to close; qa/uat/prod-review are not cascaded lower)

---

## Gate Checklists

### INTAKE Gate

#### Generic (6 checks)
1. **Title-Description Coherence** — PASS if about the same general area/goal even if worded differently. FAIL ONLY if clearly about two different things.
2. **Steps to Reproduce / Context** — new person can understand without a meeting.
3. **Definition of Done** — explicit, observable end state. FAIL if vague.
4. **Screenshots / Evidence** — INTAKE ONLY: work hasn't started, so finished output doesn't exist. PASS if current-state screenshot, output format description, or mockup present. Planning/initiative tickets auto-PASS (artifact doesn't exist yet). FAIL only if a data/UI claim is made with zero supporting material.
5. **Mandatory Fields** — all sections present with substantive content. PASS on substance, not formatting style.
6. **DE Actionability** — auto-PASS when no DE work in scope. For DE tickets: BQ path present in description (any `project.dataset.table` string, including proposed/target paths) = PASS.

#### BI Tickets (when Tableau / Power BI detected)
1. Problem Statement names dashboard, persona, business value.
2. BI Tool explicitly specified (tool + workspace/embed target).
3. **BQ path present** — scans for any `project.dataset.table` string anywhere in description. Proposed/target/wildcard paths acceptable. FAIL only if no BQ path exists at all.
4. KPIs/Metrics with calculation logic or spec reference.
5. Definition of Done — what the finished dashboard shows and how sign-off is given.
6. Screenshot/Mockup/Wireframe — any visual reference. FAIL only if none whatsoever.

### PRE-EXECUTION Gate

#### Generic (6 checks)
1. BA Inputs Complete — all 6 present, none TBD.
2. Valid DE Assignee — not Komal/Frido; Anudeep only for BI.
3. **BQ path present** — same pattern-match as INTAKE. Proposed/target paths acceptable.
4. Feasibility Assessment — review comment for T2/T3; auto-PASS for T1.
5. Dependencies Identified and Unblocked.
6. Scope Locked — no TBD/placeholder language.

#### BI Tickets
1. All 6 BI Intake inputs complete.
2. Valid BI developer assigned.
3. Granularity and filters defined.
4. Refresh cadence confirmed.
5. Upstream DE dependencies unblocked.
6. Scope locked.

### CLOSURE Gate

#### Generic (6 checks) — important rules
- **Check 1 (Acceptance Criteria):** PASS if any comment saying "done" / "Moving to Done" / closing note exists. Does NOT require metric-specific validation — a general "work done" statement is enough.
- **Check 2 (Evidence):** Google Sheets URL, Google Docs URL, ClickUp Doc link, GitHub PR link in comments all count as evidence.
- **Check 3 (QA Sign-Off):** Ashritha Akkinepally's closure notes = always PASS. Any comment from someone other than the sole assignee confirming completion also counts.
- **Check 4 (No Open Subtasks):** all subtasks closed or explicitly N/A.
- **Check 5 (Stakeholder Notified):** PASS if any team member has commented confirming done — explicit @mention not required.
- **Check 6 (Documentation):** For bug fixes / logic updates / config changes, or tickets whose title contains mismatch/discrepancy/gap/fix/bug/logic/validation/incorrect/wrong — auto-PASS (docs N/A implied by scope).

#### BI Tickets
1. All KPIs validated with before/after numbers or screenshots.
2. Published dashboard link or final screenshot attached.
3. Stakeholder/client sign-off in a comment.
4. All subtasks closed or N/A.
5. Source tables/views documented or linked.
6. Publish and access handoff confirmed.

---

## Special Logic

### New-Build / Ingestion Ticket — BQ Path Check Override

**Problem:** Tickets that *create* a new BQ table or ingestion pipeline can't provide a "confirmed" BQ path at INTAKE time — the path is the deliverable, not the source.

**Fix (Python-level, post-LLM):** After the LLM responds, `_fix_bq_check_false_fail()` scans the processed description for any string matching `project.dataset.table` (e.g. `pulse-instanthydration.instanthydration_4927_prod_raw.Northbeam_Ads_data_*`). If a path is found and the LLM marked check #3 as ❌ FAIL, it is automatically overridden to ✅ PASS. The found path is shown in the check detail. The SUMMARY line is also corrected.

This is a Python post-processor rather than an LLM instruction because Groq caches temperature=0 responses and the LLM's training priors consistently overrode prompt instructions for this check.

**Signals that identify a new-build ticket:** title/description contains "ingest", "ingestion", "pipeline", "connector", "new table", "build table", "create table", or description has a section labelled "Target BQ paths".

### Auto-Complete (CLOSURE, 5/6)

When a CLOSURE gate scores 5/6 and the one failing check is a **soft formality** (closing note missing, stakeholder @mention missing, or docs N/A not stated), SubInspector:
1. Generates a closing note using `llama-3.1-8b-instant` based on ticket description and comments.
2. Posts the closing note as a comment.
3. Moves the ticket to `complete`.
4. Posts a second comment confirming the auto-complete.

Disabled in advisory mode. Never fires when the ticket is already at `complete` or `done`.

### Failure Escalation

After **2 consecutive SubInspector FAILs** on the same gate for the same ticket:
- Gate enforcement is suspended for that ticket.
- A comment is posted tagging `@Komal Saraogi` for manual review.
- Automatic status reverts stop.

### Master Ticket Coverage

When a ticket has subtasks or its title contains "master / epic / initiative / tracker / rollout," the LLM appends a master ticket block to its report:
```
MASTER TICKET: YES
SCOPE ITEMS FOUND: [list]
SUBTASK COVERAGE: | Scope Item | Covered By | Status |
SCOPE VERDICT: FULLY COVERED / PARTIALLY COVERED / GAPS FOUND
```

### Table-Embed Processing

ClickUp descriptions often contain `[table-embed:...]` blocks. These are processed before being sent to the LLM:
- **Large tables (> 10 rows):** collapsed to `[table: N rows × M cols — reference table present ✓]` to save token budget.
- **Small tables (≤ 10 rows):** formatted as readable bullet rows so the LLM can see BQ paths, KPI definitions, etc. embedded in them.

### Bot Comment Loop Prevention (3 layers)

1. **Actor filter:** `taskStatusUpdated` events from the bot's own user ID are skipped entirely.
2. **Length filter:** `taskCommentPosted` events from the bot's user ID are skipped unless the comment is a short trigger phrase (≤ 100 chars). The bot's own evaluation reports are hundreds of chars — structurally impossible to loop.
3. **Output scrub:** Any trigger phrase (`/si check`, `/subinspector check`) hallucinated in the LLM's output is replaced with `[re-check command]` before posting.

---

## Token Budget Management

Groq free tier: **6000 TPM** per model. The system prompt (common + gate-specific) is ~1300 tokens. Max output is 1500 tokens. The remaining ~3200 tokens are for the user message. All description/comment inputs are capped before sending:

| Input | Cap |
|---|---|
| Description | 3500 chars |
| Comment head | 1200 chars |
| Comment tail | 1200 chars |
| Attachment info | 1500 chars |
| Subtask info | 800 chars |
| Full user message hard cap | 9000 chars |

---

## Backfill Scan (`/scan` endpoint)

`POST /scan?folder_id=<id>&dry_run=true/false`

Scans all tasks in a folder and posts gate comments for any that were missed (no existing SubInspector comment matching the current gate). Backfill is **comment-only** — no status reverts regardless of enforcement mode.

Used to retroactively process master tickets or tasks created before SubInspector was deployed.

---

## Key People & Routing

| Person | Role | Gate logic |
|---|---|---|
| Komal Saraogi | PM / BA | Never counts as a valid DE assignee |
| Frido | Management | Never counts as a valid DE assignee |
| Anudeep | BI developer | Valid assignee for BI tickets only |
| Ashritha Akkinepally | Team Lead | Her closure notes = QA sign-off (auto-PASS check 3) |
| Komal Saraogi | Escalation target | Tagged after 2 consecutive FAILs |

---

## Environment Variables (HF Secrets)

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | ✅ | Groq API key for LLM calls |
| `CLICKUP_API_KEY` | ✅ | ClickUp API key (also used as webhook auth) |
| `BOT_USER_ID` | ✅ | ClickUp user ID of the bot account (default: `100965864`) |
| `ENFORCEMENT_FOLDERS` | Optional | Comma-separated folder IDs for full enforcement (default: IH folder) |
| `ENFORCEMENT_SPACES` | Optional | Comma-separated space IDs for enforcement (space-level catch-all) |
| `ADVISORY_FOLDERS` | Optional | Overrides the hardcoded default of 9 client folders |
| `ADVISORY_SPACES` | Optional | Space-level advisory catch-all |

---

*Last updated: 2026-05-06*
