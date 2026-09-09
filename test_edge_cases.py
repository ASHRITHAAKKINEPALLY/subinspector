"""
SubInspector edge-case stress tests.
Runs entirely locally — no live ClickUp or Groq calls.
"""
import sys, os, re, asyncio, types, json
sys.path.insert(0, os.path.dirname(__file__))

# Windows consoles default to cp1252, which cannot encode the box-drawing and
# arrow characters this harness (and agent.py's startup log) print. Force UTF-8
# on stdout/stderr so `python test_edge_cases.py` runs anywhere without needing
# PYTHONIOENCODING set.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# ── stub out env vars and heavy imports before importing agent ──────────────
os.environ.setdefault("GROQ_API_KEY",    "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")

# Stub pdfplumber / openpyxl / docx so agent.py imports cleanly without them
for mod in ("pdfplumber", "openpyxl", "docx"):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules.setdefault("docx", types.ModuleType("docx"))
docx_mod = sys.modules["docx"]
docx_mod.Document = lambda *a, **k: None

import agent  # noqa: E402  — import after stubs

# ── helpers ─────────────────────────────────────────────────────────────────
PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append(condition)
    print(f"  {status}  {name}" + (f"\n         → {detail}" if detail else ""))

def section(title):
    print(f"\n{'─'*60}\n  {title}\n{'─'*60}")

# ── 1. GATE ROUTING ──────────────────────────────────────────────────────────
section("1. Gate Routing")

# "ready to close" must route to CLOSURE, not PRE-EXECUTION
gate, dry, _, _ = agent.determine_gate("taskStatusUpdated", "ready to close", [])
check("'ready to close' → CLOSURE (not PRE-EXECUTION)", gate == "CLOSURE", f"got {gate}")

# mixed-case status normalisation
gate, _, _, _ = agent.determine_gate("taskStatusUpdated", "In Progress", [])
check("'In Progress' (mixed case) → PRE-EXECUTION", gate == "PRE-EXECUTION", f"got {gate}")

# backlog / unknown status → no gate
gate, _, _, _ = agent.determine_gate("taskStatusUpdated", "backlog", [])
check("'backlog' → no gate (None)", gate is None, f"got {gate}")

# taskCreated always → INTAKE
gate, dry, _, _ = agent.determine_gate("taskCreated", "open", [])
check("taskCreated → INTAKE, is_dry_run=False", gate == "INTAKE" and dry == False, f"gate={gate} dry={dry}")

# /si check on a done ticket → CLOSURE with already_done=True (no revert)
comment_obj = {"id": "c1", "comment_text": "/si check", "text_content": "/si check"}
history = [{"comment": comment_obj, "id": "c1"}]
gate, dry, _, _ = agent.determine_gate("taskCommentPosted", "done", history)
check("/si check on 'done' → CLOSURE, is_dry_run=True", gate == "CLOSURE" and dry == True, f"gate={gate} dry={dry}")

# /si check on a PRE-EXEC status ticket
history2 = [{"comment": {"id": "c2", "comment_text": "/si check", "text_content": "/si check"}, "id": "c2"}]
gate, dry, _, _ = agent.determine_gate("taskCommentPosted", "in progress", history2)
check("/si check on 'in progress' → PRE-EXECUTION, dry=True", gate == "PRE-EXECUTION" and dry == True, f"gate={gate} dry={dry}")

# /si check on 'ready to close' via comment → CLOSURE (not PRE-EXEC)
history3 = [{"comment": {"id": "c3", "comment_text": "/si check", "text_content": "/si check"}, "id": "c3"}]
gate, dry, _, _ = agent.determine_gate("taskCommentPosted", "ready to close", history3)
check("/si check on 'ready to close' → CLOSURE (not PRE-EXEC)", gate == "CLOSURE", f"got {gate}")

# ── 2. SCORING LOGIC ─────────────────────────────────────────────────────────
section("2. Scoring — PASS count beats LLM's stated SCORE")

content_mismatch = """TIER: T2 — analysis
RESULT: FAIL
SCORE: 4/6
CHECKS:
| 1 | Problem Statement | ✅ PASS | ok |
| 2 | Steps to Reproduce | ✅ PASS | ok |
| 3 | Definition of Done | ✅ PASS | ok |
| 4 | Screenshots | ✅ PASS | ok |
| 5 | Mandatory Fields | ✅ PASS | ok |
| 6 | DE Actionability | ✅ PASS | ok |
SUMMARY: All checks pass."""

checks_match = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_mismatch, re.DOTALL)
score = str(checks_match.group(1).count("✅ PASS"))
passed = int(score.strip()) == 6
check("6 ✅ PASS in CHECKS overrides stated SCORE: 4/6 → passed=True", passed, f"score={score}")

# LLM returns no CHECKS section at all — fallback to SCORE regex
content_no_checks = "TIER: T1\nRESULT: PASS\nSCORE: 6/6\nSUMMARY: all good"
checks_match2 = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_no_checks, re.DOTALL)
if checks_match2:
    score2 = str(checks_match2.group(1).count("✅ PASS"))
else:
    sm = re.search(r"SCORE:\s*(\d+)/6", content_no_checks, re.IGNORECASE)
    score2 = sm.group(1) if sm else "0"
check("No CHECKS section → fallback SCORE regex returns '6'", score2 == "6", f"score={score2}")

# No CHECKS and no SCORE → score defaults to "0"
content_empty = "TIER: T1\nRESULT: FAIL\nSUMMARY: something"
checks_match3 = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_empty, re.DOTALL)
if checks_match3:
    score3 = str(checks_match3.group(1).count("✅ PASS"))
else:
    sm3 = re.search(r"SCORE:\s*(\d+)/6", content_empty, re.IGNORECASE)
    score3 = sm3.group(1) if sm3 else "0"
check("No CHECKS and no SCORE → score defaults to '0'", score3 == "0", f"score={score3}")

# Score with trailing whitespace doesn't crash int()
check("int('6 '.strip()) doesn't raise", int("6 ".strip()) == 6)

# ── 3. TRIGGER LOOP PREVENTION ───────────────────────────────────────────────
section("3. Trigger Strip — LLM hallucinating /si check in response")

content_with_trigger = "SUMMARY: Run /si check again to confirm.\nSCORE: 5/6"
for _tp in agent._TRIGGER_PATTERNS:
    content_with_trigger = _tp.sub("[re-check command]", content_with_trigger)
check("'/si check' in LLM output is replaced before posting", "/si check" not in content_with_trigger, content_with_trigger[:80])

content_with_subinspector = "Please run /subinspector check to revalidate."
for _tp in agent._TRIGGER_PATTERNS:
    content_with_subinspector = _tp.sub("[re-check command]", content_with_subinspector)
check("'/subinspector check' in LLM output is replaced", "/subinspector check" not in content_with_subinspector)

# ── 4. AUTO-COMPLETE ─────────────────────────────────────────────────────────
section("4. Auto-Complete Score Floor and Check Matching")

# Score floor: 3/6 should NOT auto-complete even if all gaps are soft
content_3_soft = """CHECKS:
| 1 | Acceptance Criteria | ✅ PASS | ok |
| 2 | Evidence | ✅ PASS | ok |
| 3 | QA Sign-Off | ✅ PASS | ok |
| 4 | No Open Subtasks | ❌ FAIL | open subtask |
| 5 | Stakeholder Notified | ❌ FAIL | missing |
| 6 | Documentation Updated | ❌ FAIL | missing |"""
can_fix, _ = agent._can_auto_complete(3, content_3_soft)
check("Score 3/6 — auto-complete blocked by floor (even if gaps are soft)", not can_fix)

# Score 5/6, only documentation failing → should auto-complete
content_5_docs = """CHECKS:
| 1 | Acceptance Criteria | ✅ PASS | ok |
| 2 | Evidence | ✅ PASS | ok |
| 3 | QA Sign-Off | ✅ PASS | ok |
| 4 | No Open Subtasks | ✅ PASS | ok |
| 5 | Stakeholder Notified | ✅ PASS | ok |
| 6 | Documentation Updated | ❌ FAIL | not confirmed |"""
can_fix, failing = agent._can_auto_complete(5, content_5_docs)
check("Score 5/6, only 'Documentation Updated' failing → auto-complete OK", can_fix, f"failing={failing}")

# Score 5/6, only evidence failing → should NOT auto-complete
content_5_evidence = """CHECKS:
| 1 | Acceptance Criteria | ✅ PASS | ok |
| 2 | Evidence | ❌ FAIL | no screenshot |
| 3 | QA Sign-Off | ✅ PASS | ok |
| 4 | No Open Subtasks | ✅ PASS | ok |
| 5 | Stakeholder Notified | ✅ PASS | ok |
| 6 | Documentation Updated | ✅ PASS | ok |"""
can_fix, failing = agent._can_auto_complete(5, content_5_evidence)
check("Score 5/6, 'Evidence' failing → auto-complete blocked (not auto-fixable)", not can_fix, f"failing={failing}")

# Score 4/6, two soft gaps → must NOT auto-complete.
# _can_auto_complete fires ONLY at exactly 5/6 (see its docstring): "Requiring
# exactly 5/6 ensures SI never auto-closes a ticket with two or more real gaps."
# Two soft gaps are still two gaps, so a human resolves them.
content_4_soft = """CHECKS:
| 1 | Acceptance Criteria | ✅ PASS | ok |
| 2 | Evidence | ✅ PASS | ok |
| 3 | QA Sign-Off | ❌ FAIL | missing |
| 4 | No Open Subtasks | ✅ PASS | ok |
| 5 | Stakeholder Notified | ❌ FAIL | missing |
| 6 | Documentation Updated | ✅ PASS | ok |"""
can_fix, failing = agent._can_auto_complete(4, content_4_soft)
check("Score 4/6, two soft gaps → auto-complete blocked (floor is exactly 5/6)", not can_fix, f"failing={failing}")

# ── 5. FAILURE COUNTER — PER-GATE ISOLATION ──────────────────────────────────
section("5. Failure Counter — Per-Gate Isolation")

raw_comments = [
    {"comment_text": "🤖 **SubInspector — PRE-EXECUTION Gate**\n❌ FAIL\nSCORE: 3/6"},
    {"comment_text": "🤖 **SubInspector — PRE-EXECUTION Gate**\n❌ FAIL\nSCORE: 4/6"},
    {"comment_text": "🤖 **SubInspector — CLOSURE Gate**\n❌ FAIL\nSCORE: 5/6"},
    {"comment_text": "Some human comment"},
]

def _count_sync(gate):
    count = 0
    for c in raw_comments:
        text = agent.extract_comment_text(c)
        gate_marker = f"SubInspector — {gate} Gate" if gate else "SubInspector"
        if gate_marker in text and "❌" in text:
            count += 1
    return count

pre_count = _count_sync("PRE-EXECUTION")
closure_count = _count_sync("CLOSURE")
check("PRE-EXECUTION failure count = 2 (not 3)", pre_count == 2, f"got {pre_count}")
check("CLOSURE failure count = 1 (isolated from PRE-EXEC)", closure_count == 1, f"got {closure_count}")

# ── 6. TABLE-EMBED PROCESSING ────────────────────────────────────────────────
section("6. Table-Embed Processing")

# Large table (>10 rows) → collapsed to summary token
large_table_cells = " | ".join([f"{r}:1 header{r}" for r in range(1, 16)])
large_table = f"[table-embed:{large_table_cells}]"
result = agent._process_table_embeds(large_table)
check("Large table (15 rows) → collapsed to summary token", "rows" in result and "table-embed" not in result, result[:80])

# Small table (≤10 rows) → formatted as bullet rows
small_cells = "1:1 Name | 1:2 BQ Path | 2:1 orders | 2:2 project.dataset.orders | 3:1 sessions | 3:2 project.dataset.sessions"
small_table = f"[table-embed:{small_cells}]"
result2 = agent._process_table_embeds(small_table)
check("Small table → formatted as readable bullets (BQ path visible)", "project.dataset" in result2, result2[:120])

# ] inside a cell value — manual scanner captures full block
tricky = "[table-embed:1:1 Header | 1:2 Value[0] | 2:1 row | 2:2 data]"
result3 = agent._process_table_embeds(tricky)
check("] inside cell value — block captured correctly (no truncation)", "table-embed:" not in result3, result3[:80])

# Multiple table-embeds in one description
multi = "Before [table-embed:1:1 A | 2:1 val] middle [table-embed:1:1 B | 2:1 val2] after"
result4 = agent._process_table_embeds(multi)
check("Multiple table-embeds in one string — both processed", "table-embed:" not in result4 and "Before" in result4 and "after" in result4, result4[:120])

# ── 7. BOT COMMENT FILTER ────────────────────────────────────────────────────
section("7. Bot Comment Filter — LLM context isolation")

bot_eval  = {"comment_text": "🤖 **SubInspector — INTAKE Gate**\nSCORE: 3/6\n❌ FAIL"}
bot_auto  = {"comment_text": "🤖 **SubInspector — Auto-Completed** | Score 6/6"}
bot_note  = {"comment_text": "🤖 **SubInspector — Auto-Generated Closing Note**\n✅ Work is done."}
human_comment = {"comment_text": "Looks good to me, moving to Done 🎉"}

def _should_skip(obj):
    text = agent.extract_comment_text(obj)
    return bool(
        text and "🤖 **SubInspector" in text and
        ("Gate**" in text or "SCORE:" in text or "Auto-Completed" in text)
    )

check("Bot gate-check report → filtered from LLM context", _should_skip(bot_eval))
check("Bot Auto-Completed message → filtered from LLM context", _should_skip(bot_auto))
check("Bot closing note (no Gate/SCORE) → NOT filtered (it's evidence)", not _should_skip(bot_note))
check("Human comment → NOT filtered", not _should_skip(human_comment))

# ── 8. BI DETECTION ──────────────────────────────────────────────────────────
section("8. BI Detection — keyword scan depth")

bi_keywords = ["tableau", "power bi", "powerbi", "pbix", "dashboard", "workbook", "report"]

# Keyword beyond 500 chars but within 1000 chars → should now detect BI
desc_keyword_at_800 = "x" * 790 + "dashboard" + " more text"
detected = any(kw in desc_keyword_at_800.lower()[:1000] for kw in bi_keywords)
check("BI keyword at char 790 detected (scan extended to 1000)", detected)

# Keyword beyond 1000 chars → should NOT detect as BI
desc_keyword_at_1100 = "x" * 1090 + "dashboard"
not_detected = not any(kw in desc_keyword_at_1100.lower()[:1000] for kw in bi_keywords)
check("BI keyword at char 1090 NOT detected (beyond scan window)", not_detected)

# ── 9. ADVISORY FOLDER ROUTING ───────────────────────────────────────────────
section("9. Advisory / Enforcement Folder Routing")

check("IH folder in ENFORCEMENT_FOLDERS", "90165998786" in agent.ENFORCEMENT_FOLDERS)
check("HexClad in ADVISORY_FOLDERS",       "90161200308" in agent.ADVISORY_FOLDERS)
check("Saxx in ADVISORY_FOLDERS",          "90161875051" in agent.ADVISORY_FOLDERS)
check("B Boutique in ADVISORY_FOLDERS",    "90169023555" in agent.ADVISORY_FOLDERS)
check("Naked & Thriving in ADVISORY_FOLDERS", "90167972037" in agent.ADVISORY_FOLDERS)
check("Javvy Coffee in ADVISORY_FOLDERS",  "90169078001" in agent.ADVISORY_FOLDERS)
check("Yum Brands in ADVISORY_FOLDERS",    "90164305799" in agent.ADVISORY_FOLDERS)
check("Momentous in ADVISORY_FOLDERS",     "90160230070" in agent.ADVISORY_FOLDERS)
check("BPN (Consulting) in ADVISORY_FOLDERS", "90020845754" in agent.ADVISORY_FOLDERS)
# OPEN QUESTION, not an assertion: BPN's DE folder (90160770330) is NOT in
# _DEFAULT_ADVISORY_FOLDERS — only BPN Consulting (90020845754) is. Whether the
# DE folder should be advisory is a config decision for the owner, so this
# reports the current state instead of asserting an intent we have not agreed.
_bpn_de = "90160770330" in agent.ADVISORY_FOLDERS
print(f"  [93mℹ INFO[0m  BPN (DE) 90160770330 in ADVISORY_FOLDERS: {_bpn_de}"
      "  — open config question, see comment above")
check("IH NOT in ADVISORY_FOLDERS (enforcement only)", "90165998786" not in agent.ADVISORY_FOLDERS)
check("Random internal folder NOT in either list",
      "99999999999" not in agent.ENFORCEMENT_FOLDERS and "99999999999" not in agent.ADVISORY_FOLDERS)

# ── DE TIME-TRACKING TRACK ───────────────────────────────────────────────────
section("DE Time-Tracking Track")

# Real shapes, taken from live ClickUp fetches and the HF webhook logs.
_TT_TASK = {
    "id": "86d4a4afx",
    "status": {"status": "complete", "type": "closed"},
    "assignees": [{"id": 100965864, "username": "Ashritha Akkinepally"}],
    "list":   {"id": "901616481255", "name": "Consulting Backlog"},
    "folder": {"id": "90169104190",  "name": "Pulse Implementation Intake"},
    "space":  {"id": "61473752"},
    "time_spent": 0,
}
_TT_HISTORY = [{
    "id": "x", "type": 1, "field": "status", "data": {"status_type": "closed"},
    "user":   {"id": 100965864},
    # ClickUp puts before/after at the TOP level of the history item, not under
    # "data" — reading them from data.after is what silently broke this track.
    "before": {"status": "development", "type": "custom", "orderindex": 2},
    "after":  {"status": "complete",    "type": "closed", "orderindex": 18},
}]


async def _tt_run(task, owners, history=None):
    """Drive the track with the network stubbed. Returns (revert_calls, comments)."""
    reverts, comments = [], []
    orig = (agent.fetch_task, agent.tracked_time_owner_ids,
            agent.revert_status, agent.post_comment)
    fetches = []

    async def _fetch(tid):
        fetches.append(tid); return task

    async def _owners(tid):
        return owners

    async def _revert(tid, status):
        reverts.append((tid, status)); return True

    async def _comment(tid, payload, reply_to_comment_id=None):
        comments.append(payload)

    agent.fetch_task = _fetch
    agent.tracked_time_owner_ids = _owners
    agent.revert_status = _revert
    agent.post_comment = _comment
    agent._TIMETRACK_IN_FLIGHT.clear()
    try:
        await agent.run_de_time_tracking_track(task["id"], history or _TT_HISTORY)
    finally:
        (agent.fetch_task, agent.tracked_time_owner_ids,
         agent.revert_status, agent.post_comment) = orig
    return reverts, comments, fetches


def _tt_case(name, *, task=None, owners=frozenset(), history=None,
             expect_revert, expect_target="development"):
    t = dict(_TT_TASK, **(task or {}))
    reverts, comments, _ = asyncio.run(_tt_run(t, owners, history))
    did = bool(reverts)
    ok = did == expect_revert
    detail = ""
    if not ok:
        detail = f"expected revert={expect_revert}, got {did}"
    elif did and reverts[0][1] != expect_target:
        ok, detail = False, f"reopened to {reverts[0][1]!r}, expected {expect_target!r}"
    # post_comment renders rich text only for a list; a dict becomes
    # comment_text and lands in ClickUp as "[object Object]".
    for payload in comments:
        if not isinstance(payload, list):
            ok, detail = False, f"comment payload is {type(payload).__name__}, must be list"
        elif not all(isinstance(b, dict) and "text" in b for b in payload):
            ok, detail = False, "every comment block needs a 'text' key"
    check(name, ok, detail)


# Scope resolution
check("DE intake folder in FOLDERS scope", "90169104190" in agent.DE_TIME_TRACKING_FOLDERS)
check("iQ Enterprise space in SPACES scope", "90167921604" in agent.DE_TIME_TRACKING_SPACES)
check("Data Engineering space in SPACES scope", "61473752" in agent.DE_TIME_TRACKING_SPACES)
check("PIP folder is excluded", "90020738121" in agent.DE_TIME_TRACKING_EXCLUDE_FOLDERS)
check("Job Description folder is excluded", "90160555567" in agent.DE_TIME_TRACKING_EXCLUDE_FOLDERS)
check("A real client folder is NOT excluded",
      "90163780691" not in agent.DE_TIME_TRACKING_EXCLUDE_FOLDERS)

# Behaviour — reopen path
_tt_case("No time logged → reopens to the pre-Complete status", expect_revert=True)
_tt_case("Only a non-assignee logged → still reopens",
         owners={"999999"}, expect_revert=True)
_tt_case("Time API blocked, task total 0ms → reopens via fallback",
         owners=None, expect_revert=True)
_tt_case("Any DE-space folder now in scope (True Classic)",
         task={"folder": {"id": "90163780691"}, "list": {"id": "77"}},
         expect_revert=True)

# Behaviour — leave-alone path
_tt_case("Assignee logged time → stays Complete",
         owners={"100965864"}, expect_revert=False)
_tt_case("Time API blocked but time exists → left alone, not reopened",
         task={"time_spent": 3600000}, owners=None, expect_revert=False)
_tt_case("Excluded folder wins over its in-scope space (PIP)",
         task={"folder": {"id": "90020738121"}, "list": {"id": "77"}},
         expect_revert=False)
_tt_case("Unconfigured space is ignored",
         task={"folder": {"id": "111"}, "list": {"id": "222"}, "space": {"id": "999"}},
         expect_revert=False)
_tt_case("Unassigned ticket is left alone", task={"assignees": []}, expect_revert=False)
_tt_case("Previous status unknown → cannot reopen dynamically",
         history=[dict(_TT_HISTORY[0], before={})], expect_revert=False)

# A non-Complete transition must not even spend an API call.
_nc = [dict(_TT_HISTORY[0], before={"status": "backlog"}, after={"status": "in progress"})]
_r, _c, _f = asyncio.run(_tt_run(dict(_TT_TASK), frozenset(), _nc))
check("Move to 'in progress' costs no fetch and no revert",
      not _f and not _r, f"fetches={len(_f)} reverts={len(_r)}")

# ── SUMMARY ──────────────────────────────────────────────────────────────────
total  = len(results)
passed_count = sum(results)
failed_count = total - passed_count
print(f"\n{'═'*60}")
print(f"  Results: {passed_count}/{total} passed  |  {failed_count} failed")
print(f"{'═'*60}\n")
sys.exit(0 if failed_count == 0 else 1)
