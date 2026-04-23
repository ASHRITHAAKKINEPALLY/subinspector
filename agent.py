import os
import re
import io
import base64
import asyncio
import traceback
import httpx
import pdfplumber
import openpyxl
from docx import Document

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CLICKUP_API_KEY = os.environ.get("CLICKUP_API_KEY")
ENFORCEMENT_FOLDERS = os.environ.get("ENFORCEMENT_FOLDERS", "90165998786").split(",")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CLICKUP_BASE = "https://api.clickup.com/api/v2"

PRE_EXEC_STATUSES = ["ready", "in progress", "in progess", "development", "code-review", "code review"]
CLOSURE_STATUSES = ["qa", "uat", "prod review", "prod-review", "complete", "done", "ready to close"]

# Trigger patterns — require a leading slash so the bot's own next-steps
# instructions never match and cause a loop.
# [ \t\xa0]+ covers regular space, tab, and non-breaking space (U+00A0)
# which ClickUp inserts when typing in the comment box.
_TRIGGER_PATTERNS = [
    re.compile(r'/subinspector[ \t\xa0]+check\b', re.IGNORECASE),
    re.compile(r'/si[ \t\xa0]+check\b',           re.IGNORECASE),
]

# User ID of the account the bot posts under — skip comments from this user
# to prevent the bot from reacting to its own comments.
BOT_USER_ID = os.environ.get("BOT_USER_ID", "100965864")

def _is_trigger(text: str) -> bool:
    return any(p.search(text) for p in _TRIGGER_PATTERNS)

SYSTEM_PROMPT = """You are SubInspector, a strict ClickUp ticket quality gate enforcer for the Instant Hydration (IH) data engineering team.

Your job: evaluate tickets against a formal 6-point gate checklist. Score each check PASS or FAIL. A ticket only passes if it scores 6/6. Never use subjective phrasing — every decision must map to a clear PASS or FAIL rule.

---

IH TEAM CONTEXT (use this to make smarter decisions):

TICKET TIER CLASSIFICATION — determine tier before scoring:
- T1 — Lightweight: Label fix, filter change, config tweak, simple display correction. No structural or logic change. Light gate — description + success criteria sufficient. Feasibility Assessment = PASS for T1 if task is self-evident and low-risk.
- T2 — Standard: Analysis task, moderate modeling, enhancement, metric mismatch + EDM fix. All 6 BA Inputs required. Feasibility Assessment required.
- T3 — Critical: Title/description contains keywords like "dashboard / dataset / model / client / logic / allocation" AND involves a new build or significant rework. Full gate — all 6 BA Inputs + feasibility assessment mandatory. Any TBD = FAIL.

SIMPLE DISCREPANCY BUGS (Bug Category = Logic Misalignment OR title contains "discrepancy/mismatch/incorrect"):
For a pure metric discrepancy with no logic change, the minimum acceptable INTAKE requires only:
1. Plain-language description of the mismatch (what number appears vs. what is expected).
2. Affected metric explicitly named.
3. One concrete validation check stated.
No full BA Input set or detailed edge cases required for simple discrepancies.

BUSINESS CONTEXT REQUIREMENT — all non-trivial tickets must include all 3 elements:
1. Rationale/Justification — WHY the change is needed (concrete business driver; NOT "requested by X" or "Komal asked for this").
2. Impact Statement — WHAT will improve and for WHOM (e.g., "reduces reconciliation time for finance team by 80%").
3. Enabled Use Cases — WHAT downstream workflows or decisions this enables.
FAIL the relevant BA Inputs check if any of these 3 elements are absent or vague.

TICKET TYPES at Instant Hydration:
- New SKU Onboarding — adding a new product/SKU to the data pipeline. These always follow a structured scope pattern with subtasks per SKU (e.g., "New SKU Addition - Master Ticket" with scope covering: product master update, product_name_mapped logic, Tableau dashboard visibility, end-to-end validation). Master tickets for onboarding typically have one subtask per SKU being onboarded.
- Bug Fix — data discrepancy, logic misalignment, or connector/pipeline bug. These use the Bug Category custom field.
- BI Dashboard — Tableau or Power BI dashboard creation/migration/maintenance.
- Data Engineering (DE) Master/Epic — umbrella tickets covering multi-subtask rollouts.

KEY PEOPLE (management/BA roles, do NOT count as DE execution resources):
- Komal Saraogi — Project Manager / BA lead. NEVER counts as a DE assignee.
- Frido — Management. NEVER counts as a DE assignee.
- Anudeep — BI developer. Counts as a valid assignee ONLY for BI tickets (Tableau/Power BI work).

IH CUSTOM FIELDS (always check these when evaluating):
- BRD — Business Requirements Document. Should be attached for any non-trivial ticket. FAIL if missing on complex tickets.
- Bug Category — Only on bug tickets. Valid values: False Alarm, Logic Misalignment, Consulting Code Bug, Pulse Code Bug, IH DE Team Code Bug, Incomplete QA, Daton Issue, Access Issue. If set, use this to understand the nature of the bug.
- Closure Status — Tracks on-time delivery. Values: Closed On Time, Closed with Extended Time, On Track, Delayed. Note: "Delayed" or "Closed with Extended Time" are NOT failures by themselves — they are informational.
- Connector — Which data connector/integration is involved (e.g., Daton, Fivetran). Use this to contextualize data source questions.

DESCRIPTION FORMAT NOTES:
- IH tickets often use structured sections with bold headers like "Problem Statement", "Expected Output", "Scope", "Steps to Reproduce", "Definition of Done", "Validation Checks", "Success Criteria", "Data Source", "Business Context".
- Tables in descriptions appear as `[table-embed:...]` markers — treat these as table evidence even if you cannot read the content.
- For New SKU Onboarding tickets, look for a "Scope (Per SKU Subtask)" section listing bullet items — each bullet = one expected subtask.
- Missing sections = FAIL for the relevant check. Placeholder text like "TBD", "N/A to fill later", "will update" = FAIL.

BIGQUERY CONVENTIONS at IH:
- Full BQ path format: `project.dataset.table` (e.g., `instant-hydration.raw_daton.shopify_products`).
- "in BQ", "use existing table", or no table path = FAIL for Data Source check.

---

GATE SELECTION RULES:
- INTAKE gate → ticket just created or status is "open"
- PRE-EXECUTION gate → ticket status is backlog, ready, in progress, development, code-review
- CLOSURE gate → ticket status is qa, uat, prod review, complete, done, ready to close

BI TICKET DETECTION:
Treat a ticket as a BI ticket when the title/description/list clearly refers to Power BI, Tableau, dashboards, PBIX, workbooks, or reports (building/migrating/maintaining, not pure backend DE work). Use BI-specific checklists for BI tickets.

---

INTAKE GATE — Generic (6 checks):
1. Title–Description Coherence & Problem Statement — PASS only if: (a) the ticket title and the problem statement describe the SAME entity/feature/system — flag any mismatch even if the words are related (e.g., title says "Subscription" but problem statement is about "Subscribers" = FAIL — these are different entities); AND (b) a clear problem statement is present that names the affected area/metric and includes a value-realization signal (why it matters). FAIL if either condition fails.
2. Steps to Reproduce / Context — PASS only if a new person can understand the issue without a meeting: navigation path, filters/date range, what to look at, or relevant business context. FAIL if absent or relies on private knowledge.
3. Definition of Done — PASS only if the ticket states an explicit, observable end state (what "done" looks like). FAIL if vague ("fix it", "resolve") or non-measurable.
4. Screenshots/Evidence — PASS only if evidence is attached or linked sufficient to verify the starting state. FAIL if missing when the claim depends on UI/output differences. NOTE: Attachments are read — check attachment contents, not just file names.
5. Mandatory Fields — PASS only if all required sections are present and non-empty: Problem Statement, Expected Output, Definition of Done, Data Source. FAIL if any required section is missing or left as a placeholder/heading-only.
6. DE Actionability — PASS only if the request is actionable without a clarifying meeting: expected output is clear, data source is specified (full BQ path), dependencies noted. FAIL if TBDs remain or data source is unconfirmed.

PRE-EXECUTION GATE — Generic (6 checks):
1. BA Inputs Complete — PASS only if all required inputs are present and complete: (1) problem statement, (2) expected output, (3) scope/edge cases + timeline, (4) validation checks, (5) success criteria, (6) data source + business context. FAIL if any is missing, incomplete, or TBD.
2. Valid DE Assignee — PASS only if at least one DE execution resource is assigned. Komal Saraogi and Frido do NOT count. For BI tickets only, Anudeep counts. FAIL if no assignee or only management/BA roles assigned.
3. Data Source Confirmed — PASS if a full BigQuery path (project.dataset.table) is provided. FAIL if missing or only generic ("in BQ", "use the normal table"). Check attachment contents for BQ paths if not in description.
4. Feasibility Assessment Present — PASS if a feasibility or technical review comment exists. Required for complex/T2/T3 tickets. PASS for straightforward T1 tickets where complexity is self-evident. FAIL if absent for multi-step or ambiguous work.
5. Dependencies Identified and Unblocked — PASS if all dependencies are recorded and resolved/unblocked. FAIL if any dependency lacks an owner or remains unresolved.
6. Scope Locked — PASS only if no TBD/placeholder language remains for any execution-critical aspect. FAIL if any scope is still open or expressed as a placeholder.

CLOSURE GATE — Generic (6 checks):
1. All Acceptance Criteria Addressed — PASS only if each criterion in the Definition of Done has explicit confirmation of completion (in comments or description). FAIL if any is missing or only implicitly assumed.
2. Evidence Attached — PASS if screenshots, query results, or before/after outputs are attached/referenced. Read attachment contents — do not just check file names. FAIL if missing when verification depends on outputs/data.
3. QA Sign-Off Present — PASS if a reviewer (not the same person who did the work) explicitly states approval or sign-off in a comment. FAIL if no sign-off comment exists.
4. No Open Subtasks or Blockers — PASS if all subtasks are closed (status: done/complete/closed) or explicitly marked N/A. FAIL if any subtask remains open.
5. Stakeholder Notified — PASS if requestor/BA/stakeholder is @mentioned or notified in comments that work is ready for review. FAIL if no explicit notification exists.
6. Documentation Updated — PASS if downstream docs, BRD, or data dictionaries are confirmed updated or explicitly marked N/A. FAIL if documentation is referenced as a deliverable but has no confirmation.

---

INTAKE GATE — BI Tickets (6 checks):
1. Problem Statement in user-story format naming the dashboard, target persona, and business value.
2. BI Tool explicitly specified (Power BI / Tableau + workspace/server/embed target).
3. Data source confirmed with full BigQuery path (project.dataset.table).
4. KPIs/Metrics defined with calculation logic or reference to a spec/BRD.
5. Definition of Done — what the finished dashboard shows and how sign-off is given.
6. Screenshot/Mockup/Wireframe attached as evidence of expected output.

PRE-EXECUTION GATE — BI Tickets (6 checks):
1. All 6 BI Intake inputs complete — none TBD or missing.
2. Valid BI developer assigned — Anudeep counts. Pure BA/PM/lead roles do not.
3. Granularity and filters defined (date range, drill-downs, slicers, row-level security if applicable).
4. Refresh cadence confirmed (live / daily / weekly / manual).
5. Upstream DE dependencies confirmed unblocked (source tables ready in BQ).
6. Scope locked — zero TBD language in any metric, layout, or filter definition.

CLOSURE GATE — BI Tickets (6 checks):
1. All KPIs validated with before/after numbers or screenshots showing correct values.
2. Published dashboard link or final screenshot attached.
3. Stakeholder/client sign-off confirmed in a comment.
4. All subtasks closed or marked N/A.
5. Source tables/views documented in ticket or linked doc.
6. Publish and access handoff confirmed (right workspace, right users have access).

---

SCORING: Count PASS items. Pass = 6/6. Below 6/6 = FAIL.

---

MASTER TICKET DETECTION & SCOPE COVERAGE CHECK:

A ticket is a MASTER TICKET if any of these are true:
- It has subtasks listed under it (field "Is Master Ticket: YES" will be set)
- Its title contains "master", "epic", "initiative", "tracker", "rollout", "project"
- Its description contains a structured list of deliverables or scope items

If the ticket IS a master ticket, perform an additional scope coverage analysis AFTER the 6-point gate check:

1. Parse the scope from the description — pay special attention to sections labelled "Scope", "Scope (Per SKU Subtask)", "Deliverables", or any bullet list of work items. For New SKU Onboarding master tickets, the scope bullets (e.g., "Product Master Update", "product_name_mapped logic", "Tableau visibility", "End-to-end validation") each represent an expected subtask.
2. Compare each scope item against the existing subtasks (by name, description, and status).
3. Identify gaps — scope items that have NO matching subtask.
4. Identify partial coverage — scope items where a subtask exists but is too vague to confirm full coverage.

Then append a MASTER TICKET SCOPE ANALYSIS section to your response with this format:

MASTER TICKET: YES
SCOPE ITEMS FOUND: [comma-separated list of scope items parsed from description]
SUBTASK COVERAGE:
| Scope Item | Covered By | Status |
|---|---|---|
| [scope item] | [subtask name or "❌ No subtask"] | [subtask status or "⚠️ MISSING"] |
SCOPE WARNINGS:
- ⚠️ [scope item] — no subtask exists for this. Recommend creating: "[suggested subtask name]"
- ⚠️ [scope item] — subtask exists but too broad to confirm full coverage
SCOPE VERDICT: [FULLY COVERED / PARTIALLY COVERED / GAPS FOUND]

If no gaps: write "SCOPE VERDICT: FULLY COVERED — all scope items have corresponding subtasks."
If gaps exist: write "SCOPE VERDICT: GAPS FOUND — X scope items have no subtask. Review and create missing subtasks before execution."

If the ticket is NOT a master ticket, skip this section entirely.

---

TIER OVERRIDE: If the ticket description or the triggering comment contains "Tier: T1", "Tier: T2", or "Tier: T3" (case-insensitive), use that tier and ignore your own inference. Otherwise infer from the ticket content.

QA SIGN-OFF RULES (Closure check #3):
- PASS only if a reviewer OTHER than the person who did the work explicitly states approval/sign-off in a comment.
- "LGTM", "looks good", "approved", "sign-off confirmed" all count — the key word is EXPLICIT. Silence ≠ approval.
- A comment by the same assignee who did the work does NOT count as QA sign-off.
- For stakeholder notification (Closure check #5): any @mention of the original requester, BA, or named stakeholder qualifies. A generic "done" with no @mention does NOT.
- For documentation (Closure check #6): ClickUp description updates, linked Google Docs/Confluence pages, or explicit "N/A — no doc deliverable" all qualify. Absence with no N/A = FAIL.

RESPONSE FORMAT — respond ONLY in this exact format:
TIER: [T1 / T2 / T3] — [one-line reason for this classification]
RESULT: PASS or FAIL
SCORE: X/6
CHECKS:
| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | [Check name] | ✅ PASS or ❌ FAIL | [one-line detail — for FAIL: exactly what is missing and what to add] |
| 2 | [Check name] | ✅ PASS or ❌ FAIL | [one-line detail] |
| 3 | [Check name] | ✅ PASS or ❌ FAIL | [one-line detail] |
| 4 | [Check name] | ✅ PASS or ❌ FAIL | [one-line detail] |
| 5 | [Check name] | ✅ PASS or ❌ FAIL | [one-line detail] |
| 6 | [Check name] | ✅ PASS or ❌ FAIL | [one-line detail] |
SUMMARY: [one sentence stating overall verdict and the most critical gap if FAIL]

[MASTER TICKET SCOPE ANALYSIS section here if applicable]"""


def extract_comment_text(obj):
    """Pull plain text from any ClickUp comment object regardless of structure."""
    if not obj:
        return ""
    for field in ("comment_text", "text_content", "text"):
        val = obj.get(field, "")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    blocks = obj.get("comment") or []
    if isinstance(blocks, list):
        parts = [b.get("text", "").strip() for b in blocks if isinstance(b, dict) and b.get("text")]
        text = " ".join(parts).strip()
        if text:
            return text
    return ""


def determine_gate(event, status, history_items):
    """Returns (gate, is_dry_run, trigger_comment_id, tier_override)."""
    status = status.lower()

    # Auto-check every new ticket at creation time (no revert — intake only)
    if event == "taskCreated":
        return "INTAKE", False, None, None

    # Status change → enforce gate, revert if it fails (is_dry_run=False)
    if event == "taskStatusUpdated":
        if any(s in status for s in PRE_EXEC_STATUSES):
            return "PRE-EXECUTION", False, None, None
        elif any(s in status for s in CLOSURE_STATUSES):
            return "CLOSURE", False, None, None

    # Manual si check comment → report only, never revert (is_dry_run=True)
    if event == "taskCommentPosted":
        trigger_comment_id = None
        comment_text = ""
        if history_items:
            item = history_items[0]
            # ClickUp may nest the comment object under "comment" or "data.comment"
            comment_obj = (
                item.get("comment")
                or (item.get("data") or {}).get("comment")
                or {}
            )
            own_id    = comment_obj.get("id") or item.get("id") or None
            parent_id = comment_obj.get("parent") or None
            # If si check was posted as a sub-comment (reply), reply to the
            # parent thread so the response appears in the same thread.
            # If it was a top-level comment, reply directly to that comment.
            trigger_comment_id = parent_id or own_id
            comment_text = extract_comment_text(comment_obj)
            if not comment_text:
                comment_text = extract_comment_text(item)
            print(f"[AGENT] comment own_id={own_id} parent_id={parent_id} → reply_to={trigger_comment_id} | text={repr(comment_text[:80])}", flush=True)

        tier_override = None
        tier_match = re.search(r"tier\s*:\s*(T[123])", comment_text, re.IGNORECASE)
        if tier_match:
            tier_override = tier_match.group(1).upper()

        if _is_trigger(comment_text):
            if any(s in status for s in PRE_EXEC_STATUSES):
                return "PRE-EXECUTION", True, trigger_comment_id, tier_override
            elif any(s in status for s in CLOSURE_STATUSES):
                return "CLOSURE", True, trigger_comment_id, tier_override
            else:
                return "INTAKE", True, trigger_comment_id, tier_override

    return None, False, None, None


async def fetch_task(task_id):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{CLICKUP_BASE}/task/{task_id}",
            headers={"Authorization": CLICKUP_API_KEY},
            params={"include_subtasks": "true"}
        )
        return response.json()


async def read_attachment(url, filename):
    """Download an attachment and extract its text content."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # ClickUp attachment URLs are pre-signed CDN links — try without auth first
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code in (401, 403):
                resp = await client.get(url, headers={"Authorization": CLICKUP_API_KEY}, follow_redirects=True)
            print(f"[AGENT] Download {filename}: status={resp.status_code} size={len(resp.content)}", flush=True)
            raw = resp.content
            ext = filename.lower().split(".")[-1] if "." in filename else ""

            # PDF
            if ext == "pdf":
                with pdfplumber.open(io.BytesIO(raw)) as pdf:
                    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                return f"[PDF: {filename}]\n{text[:3000]}"

            # Excel
            elif ext in ("xlsx", "xls"):
                wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
                lines = []
                for sheet in wb.sheetnames:
                    ws = wb[sheet]
                    lines.append(f"Sheet: {sheet}")
                    for row in ws.iter_rows(values_only=True):
                        row_str = " | ".join(str(c) if c is not None else "" for c in row)
                        if row_str.strip(" |"):
                            lines.append(row_str)
                return f"[Excel: {filename}]\n" + "\n".join(lines[:200])

            # Word
            elif ext in ("docx", "doc"):
                doc = Document(io.BytesIO(raw))
                text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                return f"[Word: {filename}]\n{text[:3000]}"

            # CSV / plain text
            elif ext in ("csv", "txt", "md"):
                return f"[{ext.upper()}: {filename}]\n{raw.decode('utf-8', errors='ignore')[:3000]}"

            # Image — use Groq vision to describe
            elif ext in ("png", "jpg", "jpeg", "gif", "webp"):
                b64 = base64.b64encode(raw).decode()
                mime = "image/png" if ext == "png" else "image/jpeg"
                async with httpx.AsyncClient(timeout=30) as vc:
                    vr = await vc.post(
                        GROQ_URL,
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                            "max_tokens": 500,
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Describe what this image shows in the context of a data/BI ticket. Include any numbers, charts, tables, or UI elements visible."},
                                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                                ]
                            }]
                        }
                    )
                    vr_data = vr.json()
                    if "choices" not in vr_data:
                        return f"[Image: {filename}] (vision API error: {vr_data.get('error', {}).get('message', vr_data)})"
                    desc = vr_data["choices"][0]["message"]["content"]
                return f"[Image: {filename}]\n{desc}"

            else:
                return f"[Attachment: {filename}] (unsupported format)"
    except Exception as e:
        return f"[Attachment: {filename}] (could not read: {e})"


async def fetch_all_replies(comment_id, client, depth=1):
    """Recursively fetch sub-comments up to depth 3 to avoid infinite recursion."""
    if depth > 3:
        return []
    indent = "  " * depth
    lines = []
    try:
        resp = await client.get(
            f"{CLICKUP_BASE}/comment/{comment_id}/reply",
            headers={"Authorization": CLICKUP_API_KEY}
        )
        replies = resp.json().get("comments", [])
        for r in replies:
            user = (r.get("user") or {}).get("username", "unknown")
            text = extract_comment_text(r)
            reply_id = r.get("id", "")
            if text:
                lines.append(f"{indent}↳ [{user}]: {text}")
            if reply_id:
                sub_replies = await fetch_all_replies(reply_id, client, depth + 1)
                lines.extend(sub_replies)
    except Exception:
        pass
    return lines


async def fetch_comments(task_id):
    """Fetch all comments. Returns (formatted_text_for_llm, raw_comment_list)."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{CLICKUP_BASE}/task/{task_id}/comment",
            headers={"Authorization": CLICKUP_API_KEY}
        )
        comments = response.json().get("comments", [])
        lines = []
        for c in comments:
            user = (c.get("user") or {}).get("username", "unknown")
            text = extract_comment_text(c)
            comment_id = c.get("id", "")
            if text:
                lines.append(f"[{user}]: {text}")
            if comment_id:
                replies = await fetch_all_replies(comment_id, client, depth=1)
                lines.extend(replies)
        return ("\n".join(lines) if lines else "None"), comments


async def evaluate_gate(gate, task, tier_override=None):
    task_id = task.get("id", "")
    description = (task.get("description") or "")[:4000]
    assignees = ", ".join(a.get("username", "") for a in task.get("assignees", [])) or "None"
    list_name = (task.get("list") or {}).get("name", "")
    folder_name = (task.get("folder") or {}).get("name", "")

    # Read attachments in parallel
    attachments = task.get("attachments", [])
    print(f"[AGENT] Attachments: {[a.get('title', a.get('file_name','?')) for a in attachments]}", flush=True)
    valid_attachments = [(a.get("url"), a.get("title", a.get("file_name", "attachment"))) for a in attachments[:5] if a.get("url")]
    if valid_attachments:
        attachment_results = await asyncio.gather(*[read_attachment(url, fn) for url, fn in valid_attachments])
        attachment_info = "\n\n".join(attachment_results)
    else:
        attachment_info = "None"

    # Fetch all subtasks in parallel
    subtask_stubs = task.get("subtasks", [])
    subtask_count = len(subtask_stubs)
    if subtask_stubs:
        async with httpx.AsyncClient(timeout=15) as st_client:
            async def _fetch_subtask(stub):
                try:
                    r = await st_client.get(f"{CLICKUP_BASE}/task/{stub.get('id')}", headers={"Authorization": CLICKUP_API_KEY})
                    return r.json()
                except Exception:
                    return stub
            subtasks = await asyncio.gather(*[_fetch_subtask(s) for s in subtask_stubs])
        subtask_lines = []
        for s in subtasks:
            st_status = (s.get("status") or {}).get("status", "unknown")
            st_assignees = ", ".join(a.get("username", "") for a in (s.get("assignees") or [])) or "unassigned"
            line = f"- [{st_status}] {s.get('name', '')} (assignee: {st_assignees})"
            desc = (s.get("description") or "").strip()[:500]
            if desc:
                line += f"\n  Scope: {desc}"
            subtask_lines.append(line)
        subtask_info = "\n".join(subtask_lines)
    else:
        subtask_info = "None (no subtasks)"

    # Fetch custom fields — resolve dropdown option IDs to display names
    custom_fields = task.get("custom_fields", [])
    cf_lines = []
    for cf in custom_fields:
        name = cf.get("name", "")
        value = cf.get("value", "")
        field_type = cf.get("type", "")
        if not name or value in (None, "", [], {}):
            continue
        # Resolve dropdown/label option IDs to human-readable names
        if field_type in ("drop_down", "labels") and value:
            options = (cf.get("type_config") or {}).get("options", [])
            id_to_name = {str(o.get("id", "")): o.get("name", "") for o in options}
            if isinstance(value, list):
                resolved = [id_to_name.get(str(v), str(v)) for v in value]
                display = ", ".join(r for r in resolved if r)
            else:
                display = id_to_name.get(str(value), str(value))
        else:
            display = str(value)
        if display:
            cf_lines.append(f"- {name}: {display}")
    custom_fields_info = "\n".join(cf_lines) if cf_lines else "None"

    # Fetch comments and return both formatted text (for LLM) and raw list (for failure counter)
    comments_text, raw_comments = await fetch_comments(task_id)

    # Cap individual sections to prevent Groq context overflow on large tickets
    comments_text_capped  = comments_text[:6000]  if len(comments_text)  > 6000  else comments_text
    attachment_info_capped = attachment_info[:8000] if len(attachment_info) > 8000 else attachment_info
    subtask_info_capped    = subtask_info[:4000]   if len(subtask_info)   > 4000  else subtask_info

    is_master = subtask_count > 0
    tier_line = f"Tier Override: {tier_override} (use this tier — do not infer)\n" if tier_override else ""
    user_message = (
        f"Gate: {gate}\n"
        f"{tier_line}"
        f"Task: {task.get('name', '')}\n"
        f"Status: {(task.get('status') or {}).get('status', '')}\n"
        f"Assignees: {assignees}\n"
        f"List: {list_name}\n"
        f"Folder: {folder_name}\n"
        f"Is Master Ticket: {'YES — has ' + str(subtask_count) + ' subtasks' if is_master else 'NO'}\n"
        f"Description:\n{description}\n\n"
        f"Comments (closing notes, QA sign-offs, evidence, sub-comments):\n{comments_text_capped}\n\n"
        f"Attachments (actual content read):\n{attachment_info_capped}\n\n"
        f"Subtasks ({subtask_count} total — full details):\n{subtask_info_capped}\n\n"
        f"Custom Fields:\n{custom_fields_info}"
    )

    # Hard cap on total prompt size — llama-3.3-70b context window is ~32k tokens
    if len(user_message) > 20000:
        user_message = user_message[:20000] + "\n\n[TRUNCATED — ticket content too large]"

    print(f"[AGENT] Sending to Groq — prompt size: {len(user_message)} chars", flush=True)

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "temperature": 0.1,
                "max_tokens": 2000,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ]
            }
        )
        data = response.json()
        if "choices" not in data:
            raise ValueError(f"Groq error (HTTP {response.status_code}): {data}")
        return data["choices"][0]["message"]["content"], raw_comments


async def post_comment(task_id, comment, reply_to_comment_id=None):
    """Post a comment on a task. If reply_to_comment_id is set, try to post as a reply inside
    that thread first; if the reply API call fails for any reason, fall back to a top-level
    comment so the message is never silently dropped."""
    async with httpx.AsyncClient(timeout=15) as client:
        if reply_to_comment_id:
            try:
                resp = await client.post(
                    f"{CLICKUP_BASE}/comment/{reply_to_comment_id}/reply",
                    headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
                    json={"comment_text": comment}
                )
                if resp.status_code < 300:
                    print(f"[AGENT] Reply posted to comment {reply_to_comment_id} (status {resp.status_code})", flush=True)
                    return
                print(f"[AGENT] Reply failed ({resp.status_code}) — falling back to top-level comment", flush=True)
            except Exception as e:
                print(f"[AGENT] Reply exception ({e}) — falling back to top-level comment", flush=True)

        # Post as a top-level comment (either no reply_to_comment_id, or reply failed above)
        resp = await client.post(
            f"{CLICKUP_BASE}/task/{task_id}/comment",
            headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
            json={"comment_text": comment}
        )
        if resp.status_code < 300:
            print(f"[AGENT] Top-level comment posted on task {task_id} (status {resp.status_code})", flush=True)
        else:
            print(f"[AGENT] ⚠️ Top-level comment also failed ({resp.status_code}): {resp.text[:200]}", flush=True)


async def revert_status(task_id, status):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{CLICKUP_BASE}/task/{task_id}",
            headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
            json={"status": status}
        )
        if resp.status_code < 300:
            print(f"[AGENT] Status reverted to '{status}' on task {task_id} (status {resp.status_code})", flush=True)
        else:
            print(f"[AGENT] ⚠️ Revert failed ({resp.status_code}): {resp.text[:200]}", flush=True)


def format_comment(gate, content, score, passed, prior_failures=0, reverted_to=None):
    """Parse LLM output and render a clean, structured ClickUp comment."""

    # ── extract pieces from LLM response ──────────────────────────────────
    tier_match   = re.search(r"TIER:\s*(.+)", content)
    summary_match = re.search(r"SUMMARY:\s*(.+)", content)
    checks_match  = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content, re.DOTALL)
    master_match  = re.search(r"(MASTER TICKET:.*)", content, re.DOTALL)

    tier_line    = tier_match.group(1).strip()    if tier_match    else "—"
    summary      = summary_match.group(1).strip() if summary_match else ""
    checks_table = checks_match.group(1).strip()  if checks_match  else ""
    master_block = master_match.group(1).strip()  if master_match  else ""

    # ── header ─────────────────────────────────────────────────────────────
    result_emoji = "✅" if passed else "❌"
    result_word  = "PASS" if passed else "FAIL"

    lines = [
        "---",
        f"🤖 **SubInspector — {gate} Gate**",
        "---",
        "",
        f"**🏷 Tier:** {tier_line}",
        f"**📊 Score:** {score}/6  |  {result_emoji} **{result_word}**",
    ]
    if reverted_to:
        lines.append(f"🔁 **Status reverted to:** `{reverted_to}`")
    lines.append("")

    # ── checks table ────────────────────────────────────────────────────────
    if checks_table:
        lines += [
            "**Gate Checks**",
            "",
            checks_table,
            "",
        ]

    # ── summary ─────────────────────────────────────────────────────────────
    if summary:
        lines += [
            "---",
            f"📝 **Summary:** {summary}",
        ]

    # ── next steps / escalation ─────────────────────────────────────────────
    if not passed:
        lines += ["", "---"]
        if prior_failures == 0:
            lines += [
                "💡 **Next Steps**",
                "- Fix every ❌ check listed above",
                "- Once updated, trigger a SubInspector re-check to re-evaluate",
            ]
        elif prior_failures == 1:
            lines += [
                "⚠️ **2nd Failure — BA Lead Consult Required**",
                "- This ticket has failed SubInspector gate checks **twice**",
                "- Please discuss with **@Komal Saraogi** before making further changes",
                "- Fix all ❌ checks above, then trigger a re-check to retry",
            ]
        else:
            lines += [
                f"🚨 **Repeated Failure ({prior_failures + 1} total) — Enforcement Suspended**",
                f"- **@Komal Saraogi** — manual review required before this ticket can proceed",
                "- Automatic gate enforcement is paused; the team must resolve this manually",
            ]

    # ── master ticket scope analysis ────────────────────────────────────────
    if master_block:
        lines += ["", "---", "**📋 Master Ticket Scope Analysis**", "", master_block]

    lines += ["", "---"]
    return "\n".join(lines)


async def count_subinspector_failures(task_id, raw_comments=None):
    """Count prior SubInspector FAIL comments. Accepts pre-fetched comments to avoid a duplicate API call."""
    try:
        if raw_comments is None:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{CLICKUP_BASE}/task/{task_id}/comment",
                    headers={"Authorization": CLICKUP_API_KEY}
                )
                raw_comments = resp.json().get("comments", [])
        count = 0
        for c in raw_comments:
            text = extract_comment_text(c)
            # Match only the bot's own structured failure comments by their signature header
            if "🤖 **SubInspector" in text and "❌" in text:
                count += 1
        return count
    except Exception:
        return 0


async def process_webhook(payload):
    event = payload.get("event")
    task_id = payload.get("task_id")
    history_items = payload.get("history_items", [])

    if not task_id or not event:
        return

    # Bot-account loop prevention — two cases:
    # 1. taskStatusUpdated from bot → always skip (prevents revert → re-evaluate loop)
    # 2. taskCommentPosted from bot → skip UNLESS it's a short bare trigger phrase
    #    (≤ 100 chars). The bot's evaluation reports are hundreds of chars; a human
    #    typing /si check is < 20 chars. This makes loops structurally impossible:
    #    even if the LLM somehow outputs the trigger phrase, the comment length will
    #    be >> 100 chars and will be skipped.
    if history_items:
        actor_id = str((history_items[0].get("user") or {}).get("id", ""))
        if actor_id == BOT_USER_ID:
            if event == "taskStatusUpdated":
                print(f"[AGENT] Skipping — taskStatusUpdated from bot account", flush=True)
                return
            if event == "taskCommentPosted":
                item = history_items[0]
                comment_obj = (
                    item.get("comment")
                    or (item.get("data") or {}).get("comment")
                    or {}
                )
                raw_text = (extract_comment_text(comment_obj) or extract_comment_text(item) or "").strip()
                # Only allow through if it looks like a human-typed trigger (short + matches pattern)
                if not (_is_trigger(raw_text) and len(raw_text) <= 100):
                    print(f"[AGENT] Skipping — bot account comment (len={len(raw_text)})", flush=True)
                    return

    try:
        task = await fetch_task(task_id)
    except Exception as e:
        print(f"[AGENT] fetch_task failed for {task_id}: {e}", flush=True)
        return

    folder_id = str((task.get("folder") or {}).get("id", ""))
    print(f"[AGENT] Folder ID: {folder_id} | In scope: {folder_id in ENFORCEMENT_FOLDERS}", flush=True)
    if folder_id not in ENFORCEMENT_FOLDERS:
        print(f"[AGENT] Skipping — not in scope", flush=True)
        return

    status = (task.get("status") or {}).get("status", "")
    previous_status = ""
    if history_items:
        before = history_items[0].get("before") or {}
        previous_status = (before.get("status", "") if isinstance(before, dict) else "") or ""

    gate, is_dry_run, trigger_comment_id, tier_override = determine_gate(event, status, history_items)
    print(f"[AGENT] Gate: {gate} | Status: {status} | Reply to: {trigger_comment_id} | Tier override: {tier_override}", flush=True)

    if not gate:
        print(f"[AGENT] No gate matched — skipping", flush=True)
        return

    try:
        content, raw_comments = await evaluate_gate(gate, task, tier_override=tier_override)
    except Exception as e:
        print(f"[AGENT] evaluate_gate failed: {e}", flush=True)
        traceback.print_exc()
        await post_comment(
            task_id,
            f"⚠️ SubInspector could not complete the {gate} gate check. Please trigger a re-check to retry.",
            reply_to_comment_id=trigger_comment_id
        )
        return

    # Strip any trigger phrase the LLM may have hallucinated into its output.
    # This is the third layer of loop prevention (after bot-account skip and
    # hardcoded-string discipline) — makes loops impossible even if the LLM
    # spontaneously generates the trigger phrase in a check detail or summary.
    for _tp in _TRIGGER_PATTERNS:
        content = _tp.sub("[re-check command]", content)

    result_match = re.search(r"RESULT:\s*(PASS|FAIL)", content, re.IGNORECASE)
    result = result_match.group(1).upper() if result_match else "FAIL"

    # Count actual ✅ PASS entries in the checks table rather than trusting
    # the LLM's stated SCORE, which it occasionally miscounts.
    checks_match = re.search(r"CHECKS:\n(.*?)(?=\nSUMMARY:|\nMASTER TICKET:|$)", content, re.DOTALL)
    if checks_match:
        score = str(checks_match.group(1).count("✅ PASS"))
    else:
        score_match = re.search(r"SCORE:\s*(\d+)/6", content, re.IGNORECASE)
        score = score_match.group(1) if score_match else "0"

    passed = int(score) == 6

    prior_failures = 0 if passed else await count_subinspector_failures(task_id, raw_comments=raw_comments)
    if not passed and prior_failures >= 2:
        print(f"[AGENT] Anti-loop triggered after {prior_failures + 1} failures — escalating to BA lead", flush=True)

    can_revert = not passed and not is_dry_run and bool(previous_status)
    comment = format_comment(gate, content, score, passed, prior_failures,
                             reverted_to=previous_status if can_revert else None)

    if can_revert:
        print(f"[AGENT] Reverting status to: {previous_status}", flush=True)
        await revert_status(task_id, previous_status)

    await post_comment(task_id, comment, reply_to_comment_id=trigger_comment_id)
