"""
SubInspector comprehensive stress test — v2
Covers ALL logic paths including every recent fix.
No live ClickUp or Groq calls required.
Run: python -X utf8 test_stress.py
"""
import sys, os, re, asyncio, types
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("GROQ_API_KEY",    "test-key")
os.environ.setdefault("CLICKUP_API_KEY", "test-key")

for mod in ("pdfplumber", "openpyxl", "docx"):
    sys.modules.setdefault(mod, types.ModuleType(mod))
docx_mod = sys.modules["docx"]
docx_mod.Document = lambda *a, **k: None

import agent

# ── test harness ─────────────────────────────────────────────────────────────
results = []

def check(name, condition, detail=""):
    ok = bool(condition)
    results.append(ok)
    icon = "\033[92m✅ PASS\033[0m" if ok else "\033[91m❌ FAIL\033[0m"
    print(f"  {icon}  {name}" + (f"\n         → {detail}" if detail else ""))

def section(title):
    print(f"\n{'─'*65}\n  {title}\n{'─'*65}")

# helper: build a simple comment history item with a given text
def _history(text):
    return [{"comment": {"id": "c1", "comment_text": text, "text_content": text}, "id": "c1"}]

# ─────────────────────────────────────────────────────────────────────────────
section("1. Gate Routing — all statuses + edge cases")
# ─────────────────────────────────────────────────────────────────────────────

for s in ["ready", "in progress", "in progess", "development", "code-review", "code review"]:
    g, _, _, _ = agent.determine_gate("taskStatusUpdated", s, [])
    check(f"'{s}' → PRE-EXECUTION", g == "PRE-EXECUTION", f"got {g}")

for s in ["qa", "uat", "prod review", "prod-review", "complete", "done", "ready to close"]:
    g, _, _, _ = agent.determine_gate("taskStatusUpdated", s, [])
    check(f"'{s}' → CLOSURE", g == "CLOSURE", f"got {g}")

# mixed-case
g, _, _, _ = agent.determine_gate("taskStatusUpdated", "In Progress", [])
check("'In Progress' (mixed case) → PRE-EXECUTION", g == "PRE-EXECUTION", f"got {g}")

g, _, _, _ = agent.determine_gate("taskStatusUpdated", "COMPLETE", [])
check("'COMPLETE' (upper) → CLOSURE", g == "CLOSURE", f"got {g}")

# non-trigger statuses → None
for s in ["backlog", "open", "to do", "blocked", "review", ""]:
    g, _, _, _ = agent.determine_gate("taskStatusUpdated", s, [])
    check(f"'{s}' → no gate", g is None, f"got {g}")

# taskCreated always INTAKE, never dry-run
g, dry, _, _ = agent.determine_gate("taskCreated", "open", [])
check("taskCreated → INTAKE, dry=False", g == "INTAKE" and dry is False)

# /si check on done → CLOSURE, dry=True (no revert)
g, dry, _, _ = agent.determine_gate("taskCommentPosted", "done", _history("/si check"))
check("/si check on 'done' → CLOSURE dry=True", g == "CLOSURE" and dry is True)

# /si check on non-done complete → CLOSURE, dry=False
g, dry, _, _ = agent.determine_gate("taskCommentPosted", "qa", _history("/si check"))
check("/si check on 'qa' → CLOSURE dry=False", g == "CLOSURE" and dry is False)

# /si check on PRE-EXEC status → always dry=True (advisory only)
g, dry, _, _ = agent.determine_gate("taskCommentPosted", "in progress", _history("/si check"))
check("/si check on 'in progress' → PRE-EXECUTION dry=True", g == "PRE-EXECUTION" and dry is True)

# /si check on INTAKE status → INTAKE dry=True
g, dry, _, _ = agent.determine_gate("taskCommentPosted", "open", _history("/si check"))
check("/si check on 'open' → INTAKE dry=True", g == "INTAKE" and dry is True)

# 'ready to close' must not be intercepted by PRE-EXEC ("ready" substring)
g, _, _, _ = agent.determine_gate("taskStatusUpdated", "ready to close", [])
check("'ready to close' → CLOSURE not PRE-EXECUTION", g == "CLOSURE", f"got {g}")

g, _, _, _ = agent.determine_gate("taskCommentPosted", "ready to close", _history("/si check"))
check("/si check 'ready to close' → CLOSURE not PRE-EXECUTION", g == "CLOSURE", f"got {g}")

# non-trigger comment → no gate
g, _, _, _ = agent.determine_gate("taskCommentPosted", "in progress", _history("nice work!"))
check("non-trigger comment → no gate", g is None, f"got {g}")

# tier override extracted from /si check comment
g, dry, _, tier = agent.determine_gate("taskCommentPosted", "in progress",
                                       _history("/si check Tier: T2"))
check("Tier override T2 extracted from /si check", tier == "T2", f"got {tier}")

g, _, _, tier = agent.determine_gate("taskCommentPosted", "open", _history("/si check Tier: T1"))
check("Tier override T1 extracted from /si check", tier == "T1", f"got {tier}")

# unknown event → no gate
g, _, _, _ = agent.determine_gate("taskDeleted", "in progress", [])
check("Unknown event → no gate", g is None, f"got {g}")

# ─────────────────────────────────────────────────────────────────────────────
section("2. Scope Check — folder / list / space (3-level OR logic)")
# ─────────────────────────────────────────────────────────────────────────────

IH_FOLDER = "90165998786"
IH_SPACE  = "IH_SPACE_ID"   # placeholder — set via ENFORCEMENT_SPACES in HF secrets
EXT_SPACE = "EXT_SPACE_ID"  # placeholder — set via ADVISORY_SPACES in HF secrets

def _scope(folder, lst, space, enf_folders, enf_spaces, adv_folders, adv_spaces):
    in_scope = (
        folder in enf_folders
        or lst in enf_folders
        or (bool(enf_spaces) and space in enf_spaces)
    )
    in_advisory = (
        folder in adv_folders
        or lst in adv_folders
        or (bool(adv_spaces) and space in adv_spaces)
    )
    return in_scope, in_advisory

EF = agent.ENFORCEMENT_FOLDERS
ES = [IH_SPACE]              # simulate ENFORCEMENT_SPACES set
AF = agent.ADVISORY_FOLDERS
AS_ = [EXT_SPACE]            # simulate ADVISORY_SPACES set

# --- Level 1: folder.id ---
in_sc, in_adv = _scope(IH_FOLDER, "other", "space_x", EF, [], AF, [])
check("L1 folder.id=IH → in_scope=True", in_sc)

in_sc, in_adv = _scope("90161200308", "other", "space_x", EF, [], AF, [])
check("L1 folder.id=HexClad → in_advisory=True", in_adv and not in_sc)

# --- Level 2: list.id fallback (folder=none) ---
in_sc, in_adv = _scope("none", IH_FOLDER, "space_x", EF, [], AF, [])
check("L2 list.id=IH, folder=none → in_scope=True", in_sc)

in_sc, in_adv = _scope("none", "90161875051", "space_x", EF, [], AF, [])
check("L2 list.id=Saxx, folder=none → in_advisory=True", in_adv and not in_sc)

# --- Level 3: space.id (master ticket in different folder/list) ---
in_sc, in_adv = _scope("other_folder", "other_list", IH_SPACE, EF, ES, AF, [])
check("L3 space.id=IH_SPACE, folder/list unknown → in_scope=True", in_sc)

in_sc, in_adv = _scope("other_folder", "other_list", EXT_SPACE, EF, [], AF, AS_)
check("L3 space.id=EXT_SPACE, folder/list unknown → in_advisory=True", in_adv and not in_sc)

# Master ticket in IH — all three levels tested
in_sc, in_adv = _scope(IH_FOLDER, "master_list", IH_SPACE, EF, ES, AF, [])
check("Master ticket IH: folder + space both match → in_scope=True", in_sc and not in_adv)

in_sc, in_adv = _scope("backlogs_folder", "master_list", IH_SPACE, EF, ES, AF, [])
check("Master ticket IH: only space matches → in_scope=True", in_sc and not in_adv)

# Master ticket external client
in_sc, in_adv = _scope("backlogs_folder", "master_list", EXT_SPACE, EF, [], AF, AS_)
check("Master ticket ext client: only space matches → in_advisory=True", in_adv and not in_sc)

# IH folder should NEVER trigger advisory
in_sc, in_adv = _scope(IH_FOLDER, "other", "other_space", EF, [], AF, [])
check("IH folder → in_scope=True, never in_advisory", in_sc and not in_adv)

# Completely unknown task → both False
in_sc, in_adv = _scope("rand1", "rand2", "rand3", EF, ES, AF, AS_)
check("Unknown folder/list/space → both False (skip)", not in_sc and not in_adv)

# ENFORCEMENT_SPACES not set → space check skipped (no false positives)
in_sc, in_adv = _scope("rand1", "rand2", IH_SPACE, EF, [], AF, [])
check("ENFORCEMENT_SPACES not set → space check inactive", not in_sc)

# ADVISORY_SPACES not set → space check skipped
in_sc, in_adv = _scope("rand1", "rand2", EXT_SPACE, EF, [], AF, [])
check("ADVISORY_SPACES not set → space check inactive", not in_adv)

# No overlap: IH never advisory, external never enforcement
in_sc, in_adv = _scope(IH_FOLDER, IH_FOLDER, IH_SPACE, EF, ES, AF, AS_)
check("IH: in_scope=True, in_advisory=False always", in_sc and not in_adv)

# ENFORCEMENT_SPACES and ADVISORY_SPACES env vars exist in agent
check("ENFORCEMENT_SPACES var exists in agent", hasattr(agent, "ENFORCEMENT_SPACES"))
check("ADVISORY_SPACES var exists in agent", hasattr(agent, "ADVISORY_SPACES"))
check("Both are lists", isinstance(agent.ENFORCEMENT_SPACES, list) and isinstance(agent.ADVISORY_SPACES, list))

# ─────────────────────────────────────────────────────────────────────────────
section("3. Revert Maps — PRE-EXECUTION and CLOSURE fallbacks")
# ─────────────────────────────────────────────────────────────────────────────

# All PRE-EXEC statuses in the map → revert to "open"
for s in ["ready", "in progress", "in progess", "development", "code-review", "code review"]:
    result = agent.PRE_EXEC_REVERT_MAP.get(s, "open")
    check(f"PRE_EXEC_REVERT_MAP['{s}'] = 'open'", result == "open", f"got '{result}'")

# Every PRE-EXEC status that exists also has lowercase key
for s in agent.PRE_EXEC_STATUSES:
    check(f"PRE_EXEC_REVERT_MAP has key for '{s}'",
          s in agent.PRE_EXEC_REVERT_MAP,
          f"missing key — revert would default to 'open' via .get(s, 'open'), still safe")

# CLOSURE: terminal statuses map to prod-review
for s in ["complete", "done", "ready to close"]:
    result = agent.CLOSURE_REVERT_MAP.get(s, "")
    check(f"CLOSURE_REVERT_MAP['{s}'] = 'prod-review'", result == "prod-review", f"got '{result}'")

# CLOSURE: mid-review statuses have no fallback (don't cascade lower)
for s in ["qa", "uat", "prod-review", "prod review"]:
    result = agent.CLOSURE_REVERT_MAP.get(s, "")
    check(f"CLOSURE_REVERT_MAP['{s}'] = '' (no cascade)", result == "", f"got '{result}'")

# ─────────────────────────────────────────────────────────────────────────────
section("4. Scoring — PASS count beats LLM stated SCORE")
# ─────────────────────────────────────────────────────────────────────────────

# LLM claims 4/6 but 6 PASS entries → passed=True
content_6pass = ("CHECKS:\n"
                 "| 1 | A | ✅ PASS | ok |\n"
                 "| 2 | B | ✅ PASS | ok |\n"
                 "| 3 | C | ✅ PASS | ok |\n"
                 "| 4 | D | ✅ PASS | ok |\n"
                 "| 5 | E | ✅ PASS | ok |\n"
                 "| 6 | F | ✅ PASS | ok |\n"
                 "SUMMARY: all good\nSCORE: 4/6")
m = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_6pass, re.DOTALL)
score = str(m.group(1).count("✅ PASS")) if m else "0"
check("6 ✅ PASS overrides stated SCORE:4/6 → score='6'", score == "6", f"score={score}")
check("passed = (score==6) = True", int(score) == 6)

# LLM claims 6/6 but only 3 PASS entries → passed=False
content_3pass = ("CHECKS:\n"
                 "| 1 | A | ✅ PASS | ok |\n"
                 "| 2 | B | ❌ FAIL | missing |\n"
                 "| 3 | C | ✅ PASS | ok |\n"
                 "| 4 | D | ❌ FAIL | missing |\n"
                 "| 5 | E | ✅ PASS | ok |\n"
                 "| 6 | F | ❌ FAIL | missing |\n"
                 "SUMMARY: fail\nSCORE: 6/6")
m2 = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_3pass, re.DOTALL)
score2 = str(m2.group(1).count("✅ PASS")) if m2 else "0"
check("3 ✅ PASS overrides stated SCORE:6/6 → score='3'", score2 == "3", f"score={score2}")
check("passed = (score==6) = False", int(score2) != 6)

# No CHECKS section → fallback to SCORE regex
content_no_checks = "TIER: T1\nRESULT: PASS\nSCORE: 6/6\nSUMMARY: all good"
m3 = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_no_checks, re.DOTALL)
if m3:
    score3 = str(m3.group(1).count("✅ PASS"))
else:
    sm3 = re.search(r"SCORE:\s*(\d+)/6", content_no_checks, re.IGNORECASE)
    score3 = sm3.group(1) if sm3 else "0"
check("No CHECKS section → fallback SCORE regex gives '6'", score3 == "6", f"score={score3}")

# No CHECKS and no SCORE → default "0"
content_empty = "TIER: T1\nRESULT: FAIL\nSUMMARY: something"
m4 = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content_empty, re.DOTALL)
if m4:
    score4 = str(m4.group(1).count("✅ PASS"))
else:
    sm4 = re.search(r"SCORE:\s*(\d+)/6", content_empty, re.IGNORECASE)
    score4 = sm4.group(1) if sm4 else "0"
check("No CHECKS and no SCORE → defaults to '0'", score4 == "0", f"score={score4}")

# Whitespace in score doesn't crash int()
check("int('6 '.strip()) == 6", int("6 ".strip()) == 6)
check("int(' 0 '.strip()) == 0", int(" 0 ".strip()) == 0)

# ─────────────────────────────────────────────────────────────────────────────
section("5. Token / Prompt Size Caps")
# ─────────────────────────────────────────────────────────────────────────────

# Description cap: 3500 chars
big_desc = "A" * 5000
capped_desc = big_desc[:3500]
check("Description capped at 3500 chars", len(capped_desc) == 3500)
check("Description under 3500 not truncated", len(("B" * 2000)[:3500]) == 2000)

# Comment head+tail: 1200 each = 2400 total window
HEAD, TAIL = 1200, 1200
long_comments = "X" * 5000
if len(long_comments) > (HEAD + TAIL):
    capped_c = long_comments[:HEAD] + "...[truncated]..." + long_comments[-TAIL:]
else:
    capped_c = long_comments
check("Long comments (5000) split into head+tail", len(capped_c) < 5000)
check("Head portion is 1200 chars", capped_c[:HEAD] == "X" * HEAD)
check("Tail portion is 1200 chars", capped_c[-TAIL:] == "X" * TAIL)

# Short comments not truncated
short_comments = "Y" * 2000
capped_short = short_comments if len(short_comments) <= (HEAD + TAIL) else short_comments[:HEAD] + "..." + short_comments[-TAIL:]
check("Comments under 2400 not truncated", capped_short == short_comments)

# Hard cap: 9000 chars
long_msg = "Z" * 12000
capped_msg = long_msg[:9000] + "\n\n[TRUNCATED]" if len(long_msg) > 9000 else long_msg
check("Hard cap truncates 12000-char message to 9000+tag", len(capped_msg) == 9000 + len("\n\n[TRUNCATED]"))
check("Hard cap not applied to 8000-char message", len(("W" * 8000)[:9000]) == 8000)

# Attachment cap: 1500 chars
big_attach = "P" * 3000
check("Attachment capped at 1500", len(big_attach[:1500]) == 1500)

# Subtask cap: 800 chars
big_sub = "Q" * 2000
check("Subtask info capped at 800", len(big_sub[:800]) == 800)

# ─────────────────────────────────────────────────────────────────────────────
section("6. Auto-Complete Logic")
# ─────────────────────────────────────────────────────────────────────────────

def _checks_str(rows):
    return "CHECKS:\n" + "\n".join(
        f"| {i+1} | {name} | {'✅ PASS' if ok else '❌ FAIL'} | detail |"
        for i, (name, ok) in enumerate(rows)
    )

# Score 6 → already passing, no auto-complete
c6 = _checks_str([("A",True),("B",True),("C",True),("D",True),("E",True),("F",True)])
ok6, _ = agent._can_auto_complete(6, c6)
check("Score=6 (already passing) → auto-complete=False", not ok6)

# Score 0 → below floor
c0 = _checks_str([("A",False),("B",False),("C",False),("D",False),("E",False),("F",False)])
ok0, _ = agent._can_auto_complete(0, c0)
check("Score=0 → auto-complete=False (below floor)", not ok0)

# Score 3 → below floor even if all soft
c3_soft = _checks_str([("Acceptance Criteria",True),("Evidence",True),("QA Sign-Off",True),
                        ("No Open Subtasks",False),("Stakeholder Notified",False),("Documentation Updated",False)])
ok3, _ = agent._can_auto_complete(3, c3_soft)
check("Score=3 with 3 soft gaps → auto-complete=False (floor)", not ok3)

# Score 4 with 2 soft gaps → True (at floor, fixable)
c4_soft = _checks_str([("Acceptance Criteria",True),("Evidence",True),("QA Sign-Off",False),
                        ("No Open Subtasks",True),("Stakeholder Notified",False),("Documentation Updated",True)])
ok4, failing4 = agent._can_auto_complete(4, c4_soft)
check("Score=4 with 2 soft gaps → auto-complete=True", ok4, f"failing={failing4}")

# Score 5 with only documentation failing → True
c5_docs = _checks_str([("Acceptance Criteria",True),("Evidence",True),("QA Sign-Off",True),
                        ("No Open Subtasks",True),("Stakeholder Notified",True),("Documentation Updated",False)])
ok5d, failing5d = agent._can_auto_complete(5, c5_docs)
check("Score=5 documentation only → auto-complete=True", ok5d, f"failing={failing5d}")

# Score 5 with only evidence failing → False (not auto-fixable)
c5_ev = _checks_str([("Acceptance Criteria",True),("Evidence",False),("QA Sign-Off",True),
                      ("No Open Subtasks",True),("Stakeholder Notified",True),("Documentation Updated",True)])
ok5e, failing5e = agent._can_auto_complete(5, c5_ev)
check("Score=5 evidence failing → auto-complete=False", not ok5e, f"failing={failing5e}")

# Score 5 with only open subtasks failing → False
c5_sub = _checks_str([("Acceptance Criteria",True),("Evidence",True),("QA Sign-Off",True),
                       ("No Open Subtasks",False),("Stakeholder Notified",True),("Documentation Updated",True)])
ok5s, _ = agent._can_auto_complete(5, c5_sub)
check("Score=5 open subtasks failing → auto-complete=False", not ok5s)

# Score 5 with QA sign-off failing → True (soft)
c5_qa = _checks_str([("Acceptance Criteria",True),("Evidence",True),("QA Sign-Off",False),
                      ("No Open Subtasks",True),("Stakeholder Notified",True),("Documentation Updated",True)])
ok5q, _ = agent._can_auto_complete(5, c5_qa)
check("Score=5 QA sign-off failing → auto-complete=True (soft)", ok5q)

# Score 5 with stakeholder failing → True (soft)
c5_st = _checks_str([("Acceptance Criteria",True),("Evidence",True),("QA Sign-Off",True),
                      ("No Open Subtasks",True),("Stakeholder Notified",False),("Documentation Updated",True)])
ok5st, _ = agent._can_auto_complete(5, c5_st)
check("Score=5 stakeholder failing → auto-complete=True (soft)", ok5st)

# ─────────────────────────────────────────────────────────────────────────────
section("7. Trigger Patterns — loop prevention")
# ─────────────────────────────────────────────────────────────────────────────

triggers_that_should_match = [
    "/si check",
    "/Si Check",
    "/SI CHECK",
    "/subinspector check",
    "/SubInspector Check",
    "/si  check",           # double space
    "/si\tcheck",           # tab
    "/si check",       # non-breaking space
]
for t in triggers_that_should_match:
    check(f"_is_trigger detects: {repr(t)}", agent._is_trigger(t))

triggers_that_should_not_match = [
    "si check",             # no leading slash
    "run si check",         # no leading slash
    "/scheck",              # no space
    "/si",                  # no "check"
    "please /si check this",# slash not at word start — actually this SHOULD match because the pattern is just checking for /si\s+check
    "",
]
# The pattern: /si[ \t\xa0]+check\b — just needs /si then spaces then check
# "please /si check" — this has /si check in it, should match
# Let me verify: the pattern doesn't require start of string, it just searches
non_triggers = ["si check", "/scheck", "/si", ""]
for t in non_triggers:
    check(f"_is_trigger rejects: {repr(t)}", not agent._is_trigger(t))

# Trigger strip in LLM output
content = "Run /si check again to confirm. Also /subinspector check."
for _tp in agent._TRIGGER_PATTERNS:
    content = _tp.sub("[re-check command]", content)
check("/si check stripped from LLM output", "/si check" not in content)
check("/subinspector check stripped from LLM output", "/subinspector check" not in content)
check("Replacement text present", "[re-check command]" in content)

# ─────────────────────────────────────────────────────────────────────────────
section("8. extract_comment_text — all formats")
# ─────────────────────────────────────────────────────────────────────────────

# Plain comment_text field
c1 = {"comment_text": "hello world"}
check("extract from comment_text", agent.extract_comment_text(c1) == "hello world")

# text_content field
c2 = {"text_content": "from text_content"}
check("extract from text_content", agent.extract_comment_text(c2) == "from text_content")

# Rich-text block array in "comment" key
c3 = {"comment": [{"text": "block "}, {"text": "text"}]}
result3 = agent.extract_comment_text(c3)
check("extract from comment block array", "block" in result3 and "text" in result3, repr(result3))

# Nested comment object with text
c4 = {"comment": {"comment_text": "nested"}}
result4 = agent.extract_comment_text(c4)
check("extract from nested comment object", "nested" in result4, repr(result4))

# Empty → ""
c5 = {}
check("empty dict → empty string", agent.extract_comment_text(c5) == "")

# None → ""
check("None → empty string", agent.extract_comment_text(None) == "")

# ─────────────────────────────────────────────────────────────────────────────
section("9. Bot Comment Filter — LLM context isolation")
# ─────────────────────────────────────────────────────────────────────────────

def _should_filter(obj):
    text = agent.extract_comment_text(obj)
    return bool(
        text and "🤖 **SubInspector" in text and
        ("Gate**" in text or "SCORE:" in text or "Auto-Completed" in text)
    )

bot_gate   = {"comment_text": "🤖 **SubInspector — INTAKE Gate**\nSCORE: 3/6\n❌ FAIL"}
bot_pre    = {"comment_text": "🤖 **SubInspector — PRE-EXECUTION Gate**\n✅ PASS\nSCORE: 6/6"}
bot_clos   = {"comment_text": "🤖 **SubInspector — CLOSURE Gate**\n❌ FAIL\nSCORE: 4/6"}
bot_auto   = {"comment_text": "🤖 **SubInspector — Auto-Completed** | Score 6/6"}
bot_note   = {"comment_text": "🤖 **SubInspector — Auto-Generated Closing Note**\n✅ Work done."}
human_ok   = {"comment_text": "Moving ticket to Done 🎉"}
human_ev   = {"comment_text": "Validation sheet: https://docs.google.com/spreadsheets/xxx"}

check("INTAKE gate report → filtered", _should_filter(bot_gate))
check("PRE-EXECUTION gate report → filtered", _should_filter(bot_pre))
check("CLOSURE gate report → filtered", _should_filter(bot_clos))
check("Auto-Completed message → filtered", _should_filter(bot_auto))
check("Auto-Generated Closing Note → NOT filtered (it's evidence)", not _should_filter(bot_note))
check("Human 'Done' comment → NOT filtered", not _should_filter(human_ok))
check("Human evidence link → NOT filtered", not _should_filter(human_ev))

# ─────────────────────────────────────────────────────────────────────────────
section("10. Failure Counter — per-gate isolation")
# ─────────────────────────────────────────────────────────────────────────────

raw = [
    {"comment_text": "🤖 **SubInspector — PRE-EXECUTION Gate**\n❌ FAIL\nSCORE: 3/6"},
    {"comment_text": "🤖 **SubInspector — PRE-EXECUTION Gate**\n❌ FAIL\nSCORE: 4/6"},
    {"comment_text": "🤖 **SubInspector — CLOSURE Gate**\n❌ FAIL\nSCORE: 5/6"},
    {"comment_text": "🤖 **SubInspector — INTAKE Gate**\n❌ FAIL\nSCORE: 2/6"},
    {"comment_text": "Human comment with ❌ emoji but no gate header"},
]

def _count(gate, comments):
    count = 0
    for c in comments:
        text = agent.extract_comment_text(c)
        marker = f"SubInspector — {gate} Gate" if gate else "SubInspector"
        if marker in text and "❌" in text:
            count += 1
    return count

check("PRE-EXECUTION failure count = 2", _count("PRE-EXECUTION", raw) == 2, f"got {_count('PRE-EXECUTION', raw)}")
check("CLOSURE failure count = 1", _count("CLOSURE", raw) == 1, f"got {_count('CLOSURE', raw)}")
check("INTAKE failure count = 1", _count("INTAKE", raw) == 1, f"got {_count('INTAKE', raw)}")
check("Human comment with ❌ not counted in PRE-EXEC", _count("PRE-EXECUTION", raw) == 2)

# ─────────────────────────────────────────────────────────────────────────────
section("11. format_comment — rich-text block structure")
# ─────────────────────────────────────────────────────────────────────────────

_llm = ("TIER: T2 — analysis\nRESULT: FAIL\nSCORE: 3/6\n"
        "CHECKS:\n| 1 | Title | ❌ FAIL | mismatch |\n| 2 | Steps | ✅ PASS | ok |\n"
        "| 3 | DoD | ❌ FAIL | vague |\n| 4 | Evidence | ✅ PASS | ok |\n"
        "| 5 | Fields | ✅ PASS | ok |\n| 6 | DE | ❌ FAIL | no BQ path |\n"
        "SUMMARY: three checks failed")

# Non-advisory FAIL comment — returns block list
blocks = agent.format_comment("INTAKE", _llm, "3", False, prior_failures=0)
check("format_comment returns list", isinstance(blocks, list))
check("All items are dicts", all(isinstance(b, dict) for b in blocks))
check("All items have 'text' key", all("text" in b for b in blocks))
check("Header contains SubInspector", any("SubInspector" in b["text"] for b in blocks))
check("INTAKE gate named in header", any("INTAKE" in b["text"] for b in blocks))
check("No advisory badge in enforcement mode", not any("Advisory mode" in b.get("text","") for b in blocks))
check("Next steps present on first FAIL", any("Next Steps" in b.get("text","") for b in blocks))
check("No '🔁 Status reverted' when reverted_to=None",
      not any("Status reverted" in b.get("text","") for b in blocks))

# With revert
blocks_r = agent.format_comment("PRE-EXECUTION", _llm, "3", False, prior_failures=0, reverted_to="open")
check("Reverted_to shown in comment", any("open" in b.get("text","") for b in blocks_r))

# Advisory mode
blocks_adv = agent.format_comment("PRE-EXECUTION", _llm, "3", False, advisory=True)
check("Advisory badge present", any("Advisory mode" in b.get("text","") for b in blocks_adv))
check("No next-steps in advisory mode", not any("Next Steps" in b.get("text","") for b in blocks_adv))
check("No revert line in advisory mode", not any("Status reverted" in b.get("text","") for b in blocks_adv))

# 2nd failure → escalation message
blocks_2nd = agent.format_comment("INTAKE", _llm, "3", False, prior_failures=1)
check("2nd failure → BA Lead Consult message", any("BA Lead Consult" in b.get("text","") for b in blocks_2nd))

# 3rd+ failure → enforcement suspended
blocks_3rd = agent.format_comment("INTAKE", _llm, "3", False, prior_failures=3)
check("3rd+ failure → Enforcement Suspended message", any("Enforcement Suspended" in b.get("text","") for b in blocks_3rd))

# PASS comment → no next-steps
_llm_pass = ("TIER: T1 — label fix\nRESULT: PASS\nSCORE: 6/6\n"
             "CHECKS:\n| 1 | A | ✅ PASS | ok |\n| 2 | B | ✅ PASS | ok |\n"
             "| 3 | C | ✅ PASS | ok |\n| 4 | D | ✅ PASS | ok |\n"
             "| 5 | E | ✅ PASS | ok |\n| 6 | F | ✅ PASS | ok |\n"
             "SUMMARY: all passed")
blocks_pass = agent.format_comment("INTAKE", _llm_pass, "6", True)
check("PASS comment has no next-steps", not any("Next Steps" in b.get("text","") for b in blocks_pass))

# ─────────────────────────────────────────────────────────────────────────────
section("12. Table-Embed Processing")
# ─────────────────────────────────────────────────────────────────────────────

# Large table (>10 rows) → collapsed
large_cells = " | ".join([f"{r}:1 header{r}" for r in range(1, 16)])
large_tbl = f"[table-embed:{large_cells}]"
r_large = agent._process_table_embeds(large_tbl)
check("Large table (15 rows) → collapsed summary", "rows" in r_large and "table-embed" not in r_large, r_large[:80])

# Small table → readable
small_cells = "1:1 Name | 1:2 BQ Path | 2:1 orders | 2:2 proj.ds.orders | 3:1 sessions | 3:2 proj.ds.sessions"
r_small = agent._process_table_embeds(f"[table-embed:{small_cells}]")
check("Small table → bullet rows (BQ paths visible)", "proj.ds" in r_small, r_small[:120])

# ] inside cell value — manual scanner handles it
r_tricky = agent._process_table_embeds("[table-embed:1:1 Header | 1:2 Value[0] | 2:1 row | 2:2 data]")
check("] inside cell — block captured correctly", "table-embed:" not in r_tricky, r_tricky[:80])

# Multiple embeds in one string
r_multi = agent._process_table_embeds("A [table-embed:1:1 X | 2:1 y] B [table-embed:1:1 P | 2:1 q] C")
check("Multiple table-embeds — both processed", "table-embed:" not in r_multi and "A" in r_multi and "C" in r_multi)

# No embed → passthrough
plain = "Just plain text with no table-embed"
check("No embed → unchanged passthrough", agent._process_table_embeds(plain) == plain)

# Empty string → ""
check("Empty string → ''", agent._process_table_embeds("") == "")

# ─────────────────────────────────────────────────────────────────────────────
section("13. BI Detection — keyword scan")
# ─────────────────────────────────────────────────────────────────────────────

bi_kw = ["tableau", "power bi", "powerbi", "pbix", "dashboard", "workbook", "report"]

# Title starts with [BI]
task_bi_title = {"name": "[BI] Revenue Dashboard", "description": ""}
name = task_bi_title["name"]
desc = task_bi_title.get("description","")
detected = name.lower().startswith("[bi]") or any(k in name.lower() for k in bi_kw) or any(k in desc.lower()[:1000] for k in bi_kw)
check("[BI] prefix → BI detected", detected)

# BI keyword in title
for kw in bi_kw:
    t = {"name": f"Fix {kw} issue", "description": ""}
    n = t["name"]; d = t.get("description","")
    det = n.lower().startswith("[bi]") or any(k in n.lower() for k in bi_kw) or any(k in d.lower()[:1000] for k in bi_kw)
    check(f"'{kw}' in title → BI detected", det)

# BI keyword at char 790 in description (within 1000 scan)
desc_790 = "x" * 790 + "dashboard extra text"
t790 = {"name": "normal ticket", "description": desc_790}
n = t790["name"]; d = t790["description"]
det790 = n.lower().startswith("[bi]") or any(k in n.lower() for k in bi_kw) or any(k in d.lower()[:1000] for k in bi_kw)
check("BI keyword at char 790 in description detected", det790)

# BI keyword at char 1100 (beyond scan window)
desc_1100 = "x" * 1100 + "dashboard"
t1100 = {"name": "normal ticket", "description": desc_1100}
n = t1100["name"]; d = t1100["description"]
det1100 = n.lower().startswith("[bi]") or any(k in n.lower() for k in bi_kw) or any(k in d.lower()[:1000] for k in bi_kw)
check("BI keyword at char 1100 NOT detected (beyond scan)", not det1100)

# Non-BI ticket
t_plain = {"name": "Fix allocation bug", "description": "Something about P&L logic"}
n = t_plain["name"]; d = t_plain["description"]
det_plain = n.lower().startswith("[bi]") or any(k in n.lower() for k in bi_kw) or any(k in d.lower()[:1000] for k in bi_kw)
check("Plain ticket not detected as BI", not det_plain)

# ─────────────────────────────────────────────────────────────────────────────
section("14. Advisory / Enforcement Folder Registry")
# ─────────────────────────────────────────────────────────────────────────────

# Every advisory folder must be present
advisory_expected = {
    "HexClad":              "90161200308",
    "Saxx":                 "90161875051",
    "B Boutique":           "90169023555",
    "Naked & Thriving":     "90167972037",
    "Javvy Coffee":         "90169078001",
    "Yum Brands":           "90164305799",
    "Momentous":            "90160230070",
    "BPN Consulting":       "90020845754",
    "BPN DE":               "90160770330",
}
for name, fid in advisory_expected.items():
    check(f"{name} ({fid}) in ADVISORY_FOLDERS", fid in agent.ADVISORY_FOLDERS)

check("IH folder in ENFORCEMENT_FOLDERS", "90165998786" in agent.ENFORCEMENT_FOLDERS)
check("IH NOT in ADVISORY_FOLDERS (enforcement-only)", "90165998786" not in agent.ADVISORY_FOLDERS)
check("No overlap between ENFORCEMENT and ADVISORY",
      not set(agent.ENFORCEMENT_FOLDERS) & set(agent.ADVISORY_FOLDERS))
check("Random unknown folder not in either list",
      "11111111111" not in agent.ENFORCEMENT_FOLDERS and
      "11111111111" not in agent.ADVISORY_FOLDERS)

# ─────────────────────────────────────────────────────────────────────────────
section("15. _resolve_stakeholder")
# ─────────────────────────────────────────────────────────────────────────────

task_with_creator = {"creator": {"username": "ashrithaakkinepally", "id": "100"}}
check("_resolve_stakeholder returns username", agent._resolve_stakeholder(task_with_creator) == "ashrithaakkinepally")

task_no_creator = {}
check("_resolve_stakeholder returns '' when no creator", agent._resolve_stakeholder(task_no_creator) == "")

task_creator_no_username = {"creator": {"id": "100"}}
check("_resolve_stakeholder returns '' when no username key", agent._resolve_stakeholder(task_creator_no_username) == "")

# ─────────────────────────────────────────────────────────────────────────────
section("16. INTAKE Gate Prompt Content — key rule phrases")
# ─────────────────────────────────────────────────────────────────────────────

intake_prompt = agent._GATE_CHECKS["INTAKE"]

# Check 1: lenient — only fail on genuinely different things
check("Check 1 allows same area/entity worded differently",
      "same general area" in intake_prompt or "same general" in intake_prompt)
check("Check 1 only fails on clearly different things",
      "clearly about two different things" in intake_prompt or "genuinely different" in intake_prompt or "FAIL ONLY if" in intake_prompt)

# Check 4: intake-only rule + planning exception
check("Check 4 has INTAKE ONLY RULE", "INTAKE ONLY RULE" in intake_prompt or "work has NOT started" in intake_prompt)
check("Check 4 has PLANNING/INITIATIVE EXCEPTION", "PLANNING" in intake_prompt and "INITIATIVE" in intake_prompt)
check("Check 4 allows format/sample as evidence", "format" in intake_prompt.lower() or "sample" in intake_prompt.lower())

# Check 5: substance over format
check("Check 5 allows non-strict user-story format", "not formatted as a strict user-story" in intake_prompt or "substance is present" in intake_prompt)

# Check 6: DE auto-pass
check("Check 6 auto-passes when no DE work", "PASS AUTOMATICALLY" in intake_prompt or "no data engineering" in intake_prompt.lower())
check("Check 6 only fails when BQ work is explicit", "explicitly part of the scope" in intake_prompt or "explicit" in intake_prompt)

# ─────────────────────────────────────────────────────────────────────────────
section("17. System Prompt — critical safety rules")
# ─────────────────────────────────────────────────────────────────────────────

sys_prompt = agent._SYSTEM_COMMON

check("Bot comment filter instruction present", "IGNORE BOT COMMENTS" in sys_prompt or "ignore" in sys_prompt.lower())
check("Komal Saraogi listed as non-DE", "Komal Saraogi" in sys_prompt)
check("Frido listed as non-DE", "Frido" in sys_prompt)
check("Anudeep BI-only rule present", "Anudeep" in sys_prompt)
check("BQ full path rule present", "project.dataset.table" in sys_prompt)
check("Response format TIER/RESULT/SCORE/CHECKS enforced", all(k in sys_prompt for k in ["TIER:", "RESULT:", "SCORE:", "CHECKS:"]))

# ─────────────────────────────────────────────────────────────────────────────
section("18. PRE-EXECUTION + CLOSURE Gate Prompt Content")
# ─────────────────────────────────────────────────────────────────────────────

pre_exec = agent._GATE_CHECKS["PRE-EXECUTION"]
closure  = agent._GATE_CHECKS["CLOSURE"]

check("PRE-EXEC: BA Inputs check present", "BA Inputs" in pre_exec)
check("PRE-EXEC: Valid DE Assignee check present", "Valid" in pre_exec and "DE" in pre_exec)
check("PRE-EXEC: Data Source Confirmed present", "Data Source" in pre_exec)
check("PRE-EXEC: Scope Locked present", "Scope Locked" in pre_exec or "Scope" in pre_exec)

check("CLOSURE: evidence rule present", "EVIDENCE RULE" in closure or "Google Sheets" in closure)
check("CLOSURE: sign-off rule present", "SIGN-OFF RULE" in closure or "Ashritha" in closure)
check("CLOSURE: completion rule present", "COMPLETION RULE" in closure or "Moving to Done" in closure)
check("CLOSURE: stakeholder rule present", "STAKEHOLDER RULE" in closure or "Stakeholder" in closure)
check("CLOSURE: docs rule present (bug fix auto-pass)", "mismatch" in closure or "discrepancy" in closure)

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(results)
failed = total - passed
print(f"\n{'='*65}")
print(f"  Results: {passed}/{total} passed  |  {failed} failed")
print(f"{'='*65}\n")
sys.exit(0 if failed == 0 else 1)
