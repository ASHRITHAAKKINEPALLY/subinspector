# SubInspector — Working Handover

*Written 2026-09-08. Live commit at time of writing: `b93e182`. Contains no secrets — safe to paste into a new chat.*

---

## 0. How to use this document

Paste this whole file into a new Claude Code session at the start, in the repo `c:\Users\Ashritha Akkinepally\subinspector`. It carries the project context, the constraints that are not discoverable from the code, and the working agreements. Section 6 is the important one — it is the list of things that have already wasted hours, and re-learning them is the main failure mode for a fresh session.

Secrets are **not** in this file. They live in the untracked `HANDOVER.md` in the repo root and in the HF Space secrets dashboard. Never paste those into a chat.

---

## 1. What SubInspector is

A Python agent (FastAPI + httpx) running on a HuggingFace Space. It receives ClickUp webhooks and enforces ticket-quality rules by posting comments and, where configured, reverting ticket status. It calls Groq for LLM evaluation. No ClickUp AI credits are used.

Owner and primary user: **Ashritha Akkinepally**, DE team lead at Instant Hydration / Saras Analytics.

---

## 2. Where things live

| Item | Value |
|---|---|
| Local repo | `c:\Users\Ashritha Akkinepally\subinspector` |
| GitHub remote | `origin` → `github.com/ASHRITHAAKKINEPALLY/subinspector` |
| HF remote | `hf` → `huggingface.co/spaces/ashakkinepally/subinspector` |
| Deploy | `git push origin main && git push hf main` |
| Health check | `https://ashakkinepally-subinspector.hf.space/health` |
| Container logs | `https://huggingface.co/spaces/ashakkinepally/subinspector?logs=container` |
| Entry point | `main.py` (FastAPI app, `/webhook`, `/scan`, `/health`) |
| All logic | `agent.py` (~2300 lines) |
| Project rules | `CLAUDE.md` (comment formatting, gate checklists, tier rules, people rules) |

**HF rebuilds automatically on push.** It does not need a manual restart. It takes roughly 45 seconds. See §6.2 for how to know when it is actually live — this matters more than it sounds.

---

## 3. Architecture: two independent tracks

`process_webhook(payload)` in `agent.py` handles every ClickUp event. Two entirely separate features run from it:

```
webhook arrives
  │
  ├─ Track B: DE time tracking      ← runs FIRST, on taskStatusUpdated only
  │    (must stay ahead of bot-loop prevention — see §6.3)
  │
  └─ falls through to ─────────────┐
                                   │
     bot-loop prevention           │
     scope check (enforcement/advisory folders)
     determine_gate()              │
     Track A: quality gates ───────┘
```

They are **mutually exclusive in purpose but both execute**. Ashritha was explicit about this and pushed back when it looked like the gates were being modified. Treat the gate code as read-only unless she asks otherwise.

---

## 4. Track A — quality gates (pre-existing)

Three gates, each scoring an LLM-produced checks table:

- **INTAKE** — fires on task creation and on `/si check`
- **PRE-EXECUTION** — fires on status → ready / development / code-review
- **CLOSURE** — fires on status → qa / uat / prod-review / complete

DE INTAKE tickets are scored against **7 required sections**: Problem Statement, Objective, Impact, Acceptance Criteria, Notes/Risks, Solution Approach, RCA. This replaced an older 6-point checklist on 2026-08-17. Full per-section pass/fail criteria are in `CLAUDE.md` §2 — read that rather than restating it.

BI tickets use a different 6-check ruleset, split into new-build and enhancement sub-tracks.

Scoring lives in `_score_checks()`. It counts rows dynamically, so a 7-section gate reports `n/7` and a 6-check gate reports `n/6`. It used to hardcode `/6`, which produced misleading `0/6` scores on 7-section tickets — fixed in `7aa4798`.

Manual trigger: a comment containing `/si check`. A tier override is `/si check Tier: T1` (or T2/T3).

---

## 5. Track B — DE time tracking (built 2026-09-08)

**Rule, exactly as Ashritha specified it:** a DE ticket may only remain in **Complete** once its **assignee** has logged time. Any user may log time, but only the assignee's counts. No minimum duration. No proof or attachment required. No validation that the time is accurate. If the assignee logged nothing, the ticket **reopens to the status it held immediately before Complete** — dynamic, never a fixed status.

**Comment identity:** `⏱ SubInspector DE Time Tracking Check`

**Code:** `run_de_time_tracking_track()` → `_de_time_tracking_decide()` in `agent.py`, plus helpers `tracked_time_owner_ids()`, `_task_time_spent_ms()`, `_extract_history_status()`, and the `_TIMETRACK_IN_FLIGHT` dedup set.

**Scope — two separate settings, both echoed by `/health`:**

| Setting | Matches | Default |
|---|---|---|
| `DE_TIME_TRACKING_FOLDERS` | `task.folder.id` **or** `task.list.id` | `90169104190` (Pulse Implementation Intake) |
| `DE_TIME_TRACKING_SPACES` | `task.space.id` | `90167921604` (iQ Enterprise Clickup Space) |

Keep these separate. A space id can never equal a folder id, so merging them silently matches nothing. This exact mistake cost several deploy cycles.

**Behavioural guarantees, all verified live:**

- Pass path is **silent** — no comment on a compliant ticket, to avoid comment spam.
- A failed time-entry lookup **never** reopens a ticket on its own.
- If the time-entry API is unavailable, it falls back to the task's `time_spent` total: `0` proves nobody logged anything → reopen; a non-zero total it cannot attribute → leave alone.
- If the previous status cannot be determined, it does **not** reopen (fail-open, logged).
- A ticket with no assignee is left alone.
- Non-Complete transitions return before any API call.

**Loop safety:** reverting produces a `taskStatusUpdated` whose new status is not `complete`, so the track no-ops. The comment produces `taskCommentPosted`, which the track ignores entirely.

---

## 6. Constraints that will waste your time if you don't know them

### 6.1 Groq: exactly one model is available

The account allows **only `openai/gpt-oss-120b`** for chat completions. Everything else 404s or is decommissioned — `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, `gemma-7b-it` were all tried and all failed. Rate limits: 30 req/min, 1K req/day, 8K tokens/min, 200K tokens/day.

There is **no fallback model**. Do not add one without checking `console.groq.com/settings/limits` first.

### 6.2 HF lies about deploy state — use the build marker

HF's API reports `stage: RUNNING` **and the new commit sha** while still serving the *previous* container. An end-to-end test was run against stale code because of this, and the resulting "it doesn't work" sent the investigation after a non-existent bug.

`main.py` carries `BUILD_MARKER`, echoed by `/health`:

```bash
curl -s https://ashakkinepally-subinspector.hf.space/health
# {"status":"ok","build":"2026-09-08-timetrack-3",
#  "de_time_tracking_folders":["90169104190"],
#  "de_time_tracking_spaces":["90167921604"]}
```

**Bump `BUILD_MARKER` on every deploy you intend to verify**, then poll `/health` until it matches before testing anything. Do not ask Ashritha to restart the Space — that was the wrong ask, repeatedly, and she is rightly tired of it.

### 6.3 `BOT_USER_ID` is Ashritha's own ClickUp account

`BOT_USER_ID = 100965864` is **her user id**, not a service account — SubInspector uses her API token. Consequence: bot-loop prevention treats **every manual status change she makes** as a bot action. Other teammates have different ids and are unaffected.

That branch also reads the new status from `history_items[0].data.after`, but ClickUp puts `before`/`after` at the **top level** of the history item (see §6.4), so the lookup yields `""` and hits an early `return`. Net effect: `taskStatusUpdated` webhooks originating from her account are dropped.

**Open question worth raising with her:** this likely means PRE-EXECUTION and CLOSURE gates never fire from *her* status changes, only via the periodic scan or manual `/si check`. Left unfixed deliberately — she asked for the gates to stay untouched. Track B sidesteps it by running before that block.

### 6.4 ClickUp webhook payload shape

`history_items[0]` keys are: `id, type, date, field, parent_id, data, source, user, before, after`.

`before` and `after` are **top level**, not nested under `data`. Each is an object whose `status` key is a **string**:

```json
"before": {"status": "development", "type": "custom", "orderindex": 2},
"after":  {"status": "complete",    "type": "closed", "orderindex": 18}
```

### 6.5 `post_comment` — list, not dict

`post_comment(task_id, comment)` wraps a **list** as a rich-text block array and treats **anything else** as plain `comment_text`. Passing `{"comment": blocks}` makes ClickUp render the comment as literally `[object Object]`. Pass the bare list. `CLAUDE.md` §1 has the required block format.

### 6.6 ClickUp URL segments tell you the id type

| URL segment | Type |
|---|---|
| `/v/f/<id>` | folder |
| `/v/l/<id>` | list |
| `/v/s/<id>` | **space** |
| `app.clickup.com/<id>/...` | workspace / team |

Ashritha calls all of these "list" or "folder" interchangeably. **Always confirm with an API call** before wiring an id into config. `3369097` in every URL is the workspace, not a space — comparing `space_id` to it silently matched nothing for a full deploy cycle.

### 6.7 Status names vary per list

Even within one space, lists have different status sets. `Kind Water Tickets` uses `backlog / ready / development / … / complete`; `List` uses `to do / planning / in progress / … / complete / cancelled`. Both contain a status literally named `complete`, differing only in ClickUp `type` (`closed` vs `done`). Track B matches on the **name**. Before adding a new scope, check its lists actually have a `complete` status.

---

## 7. Key IDs

| Thing | ID |
|---|---|
| Workspace / team | `3369097` |
| Bot user (= Ashritha) | `100965864` |
| Enforcement folder (Instant Hydration) | `90165998786` |
| DE time-tracking folder — Pulse Implementation Intake | `90169104190` |
| ↳ its space | `61473752` |
| ↳ list used in testing — Consulting Backlog | `901616481255` |
| DE time-tracking space — iQ Enterprise Clickup Space | `90167921604` |
| ↳ lists | `901616638452` List, `901616638460` Kind Water Tickets, `901616723868` Template, `901616723945` Ulife |
| Test ticket used throughout | `86d4a4afx` "test DE Task Template 2025" |

Advisory folders (comment-only, no enforcement) are listed in `_DEFAULT_ADVISORY_FOLDERS` in `agent.py`.

---

## 8. Working agreements with Ashritha

These are earned the hard way. Following them is most of the job.

1. **Diagnose from real data before editing.** Fetch the actual task, read the actual payload, read the error text literally. Five consecutive speculative pushes on the Groq model, then three more on this feature, produced nothing but frustration — *"how many times should I say this is not working."* If the constraint lives outside the repo (provider access, token permissions, HF secrets), go get that fact instead of iterating on code.

2. **Do the testing yourself.** She has ClickUp MCP tools available to you — use them to drive a full live test rather than asking her to click through it. She has said plainly: *"you only do it."*

3. **Verify before declaring done.** Push → confirm `/health` marker → run the functional test → *then* report. Never say "deployed" off a successful `git push`.

4. **Do not touch the gate logic** when working on time tracking. She checks. The feature landed as **173 additions, 0 deletions** against the pre-feature tree, and `git diff <base> --numstat -- agent.py` is a good way to prove it.

5. **Keep it terse and factual.** No victory laps, no restating what she already knows. Lead with the result.

---

## 9. Current state

**Working and verified live:**
- INTAKE gate on the 7-section DE format, scoring `n/7` correctly
- Groq calls on `openai/gpt-oss-120b`
- DE time-tracking track on both the folder and the space scope
- `/health` build marker and scope introspection

**Uncommitted in the working tree:** `CLAUDE.md` (the 7-section INTAKE update — still unstaged from earlier), plus various untracked scratch files (`cleanup_spam*.py`, `hf_logs_*.txt`, `test_*.py`, `ticket_data.py`, `build_deck.js`, `HANDOVER.md`, this file).

**Left as-is on the test ticket `86d4a4afx`:** status Complete, 15m logged, one FAIL comment from testing. Ashritha was told; she can clear them.

**Known open items:**
- §6.3 — gates likely not firing on Ashritha's own status changes. Raised, not fixed.
- Spam comments from the 2026-07-28 incident were never cleaned up (deferred as non-critical).
- A prior audit found 12 logic bugs in `agent.py` (4 CRITICAL). Four were fixed in `c2468e2`; the remainder were never worked through.

---

## 10. How to test end to end

Using the ClickUp MCP tools, no user involvement needed:

```
1. curl /health              → confirm BUILD_MARKER matches what you pushed
2. clickup_get_task          → note current status, folder/list/space, assignees, time_spent
3. clickup_update_task       → set a known pre-Complete status (e.g. "development")
4. clickup_update_task       → set "complete"
5. sleep ~15s
6. clickup_get_task          → expect status back at "development"
   clickup_get_task_comments → expect the ⏱ comment, rendered (not "[object Object]")
```

For the pass path, `clickup_add_time_entry` 15m as the assignee first, then move to Complete and confirm it **stays** and posts nothing.

For a space-scoped test, `clickup_create_task` a temp task in `901616638452`, run the above, then `clickup_delete_task` it.

A local harness covering 10 branches (pass, fail, non-assignee-only, API-blocked ± time, out-of-scope, space-scoped, unconfigured space, unassigned, wrong transition) was used during development; it monkeypatches `fetch_task` / `tracked_time_owner_ids` / `revert_status` / `post_comment` and asserts the comment payload is a `list`. Worth recreating if you touch this code.

---

## 11. Commits from this session

| Commit | What |
|---|---|
| `e96f85c` `53dd8a6` `bf9321c` | Groq fallback attempts — all wrong, superseded. Kept as a record of the guessing loop. |
| `6b81de8` | Groq → `openai/gpt-oss-120b`, the only available model |
| `7f3ff7f` | INTAKE gate: report Solution Approach and RCA as separate rows 6 and 7 |
| `7aa4798` | `_score_checks` counts rows dynamically instead of hardcoding `/6` |
| `24c7201` `b49b1ab` `7903fca` `277a7f0` | DE time-tracking first attempts — did not fire, superseded |
| `06449d3` | Time-tracking track made to actually fire: ordering, payload path, scope |
| `055981e` | `/health` build marker + scope introspection |
| `bd38d50` | Comment posted as rich text rather than a stringified dict |
| `b93e182` | Space scope added for the iQ Enterprise space |
