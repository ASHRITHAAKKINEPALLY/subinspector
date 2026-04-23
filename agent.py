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

print(f"[AGENT] Startup check — GROQ_API_KEY={'SET (' + GROQ_API_KEY[:8] + '...)' if GROQ_API_KEY else 'MISSING ⚠️'}", flush=True)
print(f"[AGENT] Startup check — CLICKUP_API_KEY={'SET (' + CLICKUP_API_KEY[:8] + '...)' if CLICKUP_API_KEY else 'MISSING ⚠️'}", flush=True)
print(f"[AGENT] Startup check — ENFORCEMENT_FOLDERS={ENFORCEMENT_FOLDERS}", flush=True)

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

# Shared context sent for every gate evaluation (~600 tokens)
_SYSTEM_COMMON = """You are SubInspector, a ClickUp ticket quality gate enforcer for the Instant Hydration (IH) DE team.
Evaluate tickets against a 6-point gate checklist. Score each check PASS or FAIL. Pass = 6/6. Never use subjective phrasing.

TIER CLASSIFICATION (determine first):
- T1: Label fix, filter change, config tweak. Light gate — description + success criteria sufficient.
- T2: Analysis, moderate modeling, enhancement. All 6 BA Inputs required.
- T3: Title/description contains "dashboard/dataset/model/client/logic/allocation" + new build. Full gate, any TBD = FAIL.
If comment/description contains "Tier: T1/T2/T3", use that tier.

SIMPLE DISCREPANCY BUGS (Bug Category = Logic Misalignment OR title has "discrepancy/mismatch/incorrect"):
Minimum INTAKE requires only: (1) plain description of mismatch, (2) affected metric named, (3) one validation check.

KEY PEOPLE — do NOT count as DE assignees: Komal Saraogi (PM/BA), Frido (management). Anudeep = valid only for BI tickets.
BIGQUERY: Full path required: project.dataset.table. "in BQ" or no path = FAIL.
BI TICKET: Treat as BI if title/description references Tableau, Power BI, dashboards, PBIX, workbooks.
Missing sections = FAIL. Placeholder text ("TBD", "N/A to fill later", "will update") = FAIL.

MASTER TICKET: If ticket has subtasks OR title contains master/epic/initiative/tracker/rollout, append after gate checks:
MASTER TICKET: YES
SCOPE ITEMS FOUND: [list from description]
SUBTASK COVERAGE: | Scope Item | Covered By | Status |
SCOPE VERDICT: FULLY COVERED / PARTIALLY COVERED / GAPS FOUND

RESPONSE FORMAT (strict):
TIER: [T1/T2/T3] — [reason]
RESULT: PASS or FAIL
SCORE: X/6
CHECKS:
| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | [name] | ✅ PASS or ❌ FAIL | [one-line — for FAIL: what is missing and what to add] |
| 2 | [name] | ✅ PASS or ❌ FAIL | [detail] |
| 3 | [name] | ✅ PASS or ❌ FAIL | [detail] |
| 4 | [name] | ✅ PASS or ❌ FAIL | [detail] |
| 5 | [name] | ✅ PASS or ❌ FAIL | [detail] |
| 6 | [name] | ✅ PASS or ❌ FAIL | [detail] |
SUMMARY: [one sentence verdict + most critical gap if FAIL]"""

# Gate-specific checklists (~250 tokens each) — only the relevant one is sent
_GATE_CHECKS = {
    "INTAKE": """
INTAKE GATE — Generic (6 checks):
1. Title-Description Coherence — title and problem statement describe the SAME entity. FAIL if mismatch even if related.
2. Steps to Reproduce / Context — new person can understand without a meeting: navigation, filters, date range, context.
3. Definition of Done — explicit, observable end state stated. FAIL if vague ("fix it") or unmeasurable.
4. Screenshots/Evidence — evidence attached/linked to verify starting state. FAIL if absent when UI/output claim made.
5. Mandatory Fields — Problem Statement, Expected Output, Definition of Done, Data Source all present and non-empty.
6. DE Actionability — expected output clear, full BQ path provided, no TBDs. Actionable without a meeting.

INTAKE GATE — BI Tickets (use instead of Generic when BI ticket detected):
1. Problem Statement names dashboard, target persona, and business value.
2. BI Tool explicitly specified (Power BI / Tableau + workspace/embed target).
3. Data source confirmed with full BigQuery path.
4. KPIs/Metrics defined with calculation logic or spec/BRD reference.
5. Definition of Done — what the finished dashboard shows and how sign-off is given.
6. Screenshot/Mockup/Wireframe attached as evidence of expected output.""",

    "PRE-EXECUTION": """
PRE-EXECUTION GATE — Generic (6 checks):
1. BA Inputs Complete — all 6 present: problem statement, expected output, scope/edge cases+timeline, validation checks, success criteria, data source+business context. FAIL if any missing or TBD.
2. Valid DE Assignee — at least one DE person assigned (not Komal/Frido; Anudeep only for BI). FAIL if only PM/BA assigned.
3. Data Source Confirmed — full BQ path (project.dataset.table). FAIL if generic or absent.
4. Feasibility Assessment — technical review comment exists for T2/T3. PASS for T1 if self-evident. FAIL if absent for complex work.
5. Dependencies Identified and Unblocked — all dependencies recorded with owners and unblocked.
6. Scope Locked — no TBD/placeholder language in any execution-critical aspect.

PRE-EXECUTION GATE — BI Tickets (use instead of Generic when BI ticket detected):
1. All 6 BI Intake inputs complete — none TBD.
2. Valid BI developer assigned (Anudeep counts; PM/BA do not).
3. Granularity and filters defined (date range, drill-downs, slicers, row-level security).
4. Refresh cadence confirmed (live/daily/weekly/manual).
5. Upstream DE dependencies unblocked (source tables ready in BQ).
6. Scope locked — zero TBD in any metric, layout, or filter definition.""",

    "CLOSURE": """
CLOSURE GATE — Generic (6 checks):
1. Acceptance Criteria Addressed — each DoD item has explicit confirmation in comments or description.
2. Evidence Attached — screenshots, query results, or before/after outputs attached/referenced.
3. QA Sign-Off — reviewer OTHER than the assignee explicitly states approval ("LGTM", "approved", "sign-off confirmed"). Silence ≠ approval. Same assignee's comment does NOT count.
4. No Open Subtasks — all subtasks closed (done/complete) or explicitly marked N/A.
5. Stakeholder Notified — @mention of requester/BA/stakeholder confirming work is ready. Generic "done" without @mention = FAIL.
6. Documentation Updated — downstream docs/BRD updated or explicitly marked N/A. Absence with no N/A = FAIL.

CLOSURE GATE — BI Tickets (use instead of Generic when BI ticket detected):
1. All KPIs validated with before/after numbers or screenshots.
2. Published dashboard link or final screenshot attached.
3. Stakeholder/client sign-off confirmed in a comment.
4. All subtasks closed or marked N/A.
5. Source tables/views documented in ticket or linked doc.
6. Publish and access handoff confirmed (right workspace, right users have access)."""
}


def get_system_prompt(gate: str) -> str:
    """Return a gate-specific system prompt (~850 tokens vs 2700 for the monolithic version)."""
    checks = _GATE_CHECKS.get(gate, _GATE_CHECKS["INTAKE"])
    return _SYSTEM_COMMON + "\n" + checks


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
            own_id    = (comment_obj.get("id") if isinstance(comment_obj, dict) else None) or item.get("id") or None
            parent_id = (comment_obj.get("parent") if isinstance(comment_obj, dict) else None) or None
            # If si check was posted as a sub-comment (reply), reply to the
            # parent thread so the response appears in the same thread.
            # If it was a top-level comment, reply directly to that comment.
            trigger_comment_id = parent_id or own_id
            comment_text = extract_comment_text(comment_obj) or extract_comment_text(item) or ""
            print(f"[AGENT] determine_gate: own_id={own_id} parent_id={parent_id} → reply_to={trigger_comment_id} | text={repr(comment_text[:80])}", flush=True)

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
        else:
            print(f"[AGENT] determine_gate: no trigger found in comment text={repr(comment_text[:80])}", flush=True)

    return None, False, None, None


async def fetch_task(task_id):
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{CLICKUP_BASE}/task/{task_id}",
                    headers={"Authorization": CLICKUP_API_KEY},
                    params={"include_subtasks": "true"}
                )
                raw = response.text
                if not raw or not raw.strip():
                    print(f"[AGENT] fetch_task attempt {attempt+1}: empty response (HTTP {response.status_code})", flush=True)
                    if attempt < 2:
                        await asyncio.sleep(3)
                        continue
                    raise ValueError(f"ClickUp returned empty response after 3 attempts (HTTP {response.status_code})")
                try:
                    data = response.json()
                except Exception:
                    raise ValueError(f"ClickUp non-JSON response (HTTP {response.status_code}): {raw[:200]}")
                if response.status_code >= 400 or "err" in data or not data.get("id"):
                    raise ValueError(f"ClickUp task fetch failed (HTTP {response.status_code}): {str(data)[:300]}")
                return data
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            print(f"[AGENT] fetch_task network error attempt {attempt+1}: {e}", flush=True)
            if attempt < 2:
                await asyncio.sleep(3)
            else:
                raise
    raise ValueError(f"fetch_task failed after 3 attempts for {task_id}")


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
    """Fetch all comments. Returns (formatted_text_for_llm, raw_comment_list). Never raises."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{CLICKUP_BASE}/task/{task_id}/comment",
                headers={"Authorization": CLICKUP_API_KEY}
            )
            try:
                body = response.json()
            except Exception:
                print(f"[AGENT] fetch_comments: non-JSON response (HTTP {response.status_code})", flush=True)
                return "None", []
            comments = body.get("comments", []) if isinstance(body, dict) else []
            lines = []
            for c in comments:
                user = (c.get("user") or {}).get("username", "unknown")
                text = extract_comment_text(c)
                comment_id = c.get("id", "")
                if text:
                    lines.append(f"[{user}]: {text}")
                if comment_id:
                    try:
                        replies = await fetch_all_replies(comment_id, client, depth=1)
                        lines.extend(replies)
                    except Exception:
                        pass
            return ("\n".join(lines) if lines else "None"), comments
    except Exception as e:
        print(f"[AGENT] fetch_comments failed entirely: {e}", flush=True)
        traceback.print_exc()
        return "None", []


async def evaluate_gate(gate, task, tier_override=None):
    task_id = task.get("id", "")
    description = (task.get("description") or "")[:4000]
    assignees = ", ".join(a.get("username", "") for a in task.get("assignees", [])) or "None"
    list_name = (task.get("list") or {}).get("name", "")
    folder_name = (task.get("folder") or {}).get("name", "")

    # Read attachments in parallel — use return_exceptions so one bad attachment never kills the whole eval
    attachments = task.get("attachments", [])
    print(f"[AGENT] Attachments: {[a.get('title', a.get('file_name','?')) for a in attachments]}", flush=True)
    valid_attachments = [(a.get("url"), a.get("title", a.get("file_name", "attachment"))) for a in attachments[:5] if a.get("url")]
    if valid_attachments:
        try:
            attachment_results = await asyncio.gather(
                *[read_attachment(url, fn) for url, fn in valid_attachments],
                return_exceptions=True
            )
            attachment_info = "\n\n".join(
                r if isinstance(r, str) else f"[Attachment error: {r}]"
                for r in attachment_results
            )
        except Exception as e:
            print(f"[AGENT] Attachment gather failed: {e}", flush=True)
            attachment_info = "None"
    else:
        attachment_info = "None"

    # Fetch all subtasks in parallel — return_exceptions so one failing subtask doesn't abort
    subtask_stubs = task.get("subtasks", [])
    subtask_count = len(subtask_stubs)
    if subtask_stubs:
        try:
            async with httpx.AsyncClient(timeout=15) as st_client:
                async def _fetch_subtask(stub):
                    try:
                        r = await st_client.get(f"{CLICKUP_BASE}/task/{stub.get('id')}", headers={"Authorization": CLICKUP_API_KEY})
                        d = r.json()
                        return d if isinstance(d, dict) and d.get("id") else stub
                    except Exception:
                        return stub
                subtasks_raw = await asyncio.gather(
                    *[_fetch_subtask(s) for s in subtask_stubs],
                    return_exceptions=True
                )
                subtasks = [s if isinstance(s, dict) else stub for s, stub in zip(subtasks_raw, subtask_stubs)]
        except Exception as e:
            print(f"[AGENT] Subtask gather failed: {e}", flush=True)
            subtasks = subtask_stubs
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

    # Cap sections tightly — system prompt is ~850 tokens, output ~1500 tokens,
    # leaving ~3500 tokens (~14000 chars) for the user message on the 6k TPM free tier.
    comments_text_capped  = comments_text[:2500]
    attachment_info_capped = attachment_info[:2500]
    subtask_info_capped    = subtask_info[:1500]

    is_master = subtask_count > 0

    # Detect BI tickets in Python so the LLM doesn't have to guess
    task_name = task.get("name", "")
    bi_keywords = ["tableau", "power bi", "powerbi", "pbix", "dashboard", "workbook", "report"]
    is_bi = (
        task_name.lower().startswith("[bi]")
        or any(kw in task_name.lower() for kw in bi_keywords)
        or any(kw in (task.get("description") or "").lower()[:500] for kw in bi_keywords)
    )
    bi_line = "Ticket Type: BI — use the BI-specific checklist, NOT the generic checklist.\n" if is_bi else "Ticket Type: DE (non-BI) — use the generic checklist.\n"
    print(f"[AGENT] BI detected: {is_bi}", flush=True)

    tier_line = f"Tier Override: {tier_override} (use this tier — do not infer)\n" if tier_override else ""
    user_message = (
        f"Gate: {gate}\n"
        f"{bi_line}"
        f"{tier_line}"
        f"Task: {task_name}\n"
        f"Status: {(task.get('status') or {}).get('status', '')}\n"
        f"Assignees: {assignees}\n"
        f"List: {list_name}\n"
        f"Folder: {folder_name}\n"
        f"Is Master Ticket: {'YES — has ' + str(subtask_count) + ' subtasks' if is_master else 'NO'}\n"
        f"NOTE: [table-embed:...] markers in the description are embedded data tables and count as evidence.\n"
        f"Description:\n{description}\n\n"
        f"Comments (closing notes, QA sign-offs, evidence, sub-comments):\n{comments_text_capped}\n\n"
        f"Attachments (actual content read):\n{attachment_info_capped}\n\n"
        f"Subtasks ({subtask_count} total — full details):\n{subtask_info_capped}\n\n"
        f"Custom Fields:\n{custom_fields_info}"
    )

    # Hard cap — keep total tokens under 4000 chars user msg to stay within 6k TPM free tier
    if len(user_message) > 10000:
        user_message = user_message[:10000] + "\n\n[TRUNCATED]"

    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    print(f"[AGENT] Sending to Groq — prompt size: {len(user_message)} chars", flush=True)

    class _RateLimitError(Exception):
        pass

    async def _call_groq(model: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.1,
                    "max_tokens": 1500,
                    "messages": [
                        {"role": "system", "content": get_system_prompt(gate)},
                        {"role": "user", "content": user_message}
                    ]
                }
            )
            if response.status_code == 429:
                raise _RateLimitError(f"rate limited on {model}")
            try:
                data = response.json()
            except Exception:
                raise ValueError(f"non-JSON response (HTTP {response.status_code}): {response.text[:400]}")
            if "choices" not in data:
                raise ValueError(f"Groq error on {model} (HTTP {response.status_code}): {data}")
            return data["choices"][0]["message"]["content"]

    # Strategy:
    # 1. Try llama-3.3-70b-versatile once — best quality, but strict rate limit (6k TPM free).
    # 2. On rate limit → immediately fall back to llama-3.1-8b-instant (20k TPM, no wait).
    # 3. Retry 8b-instant up to 3× with short waits if needed.
    last_error = None
    primary, fallback = "llama-3.3-70b-versatile", "llama-3.1-8b-instant"

    try:
        content = await _call_groq(primary)
        print(f"[AGENT] Groq OK — model={primary}", flush=True)
        return content, raw_comments
    except _RateLimitError as e:
        print(f"[AGENT] {primary} rate limited — switching to {fallback} immediately", flush=True)
        last_error = e
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        print(f"[AGENT] {primary} network error: {e} — switching to {fallback}", flush=True)
        last_error = e
    except Exception as e:
        print(f"[AGENT] {primary} failed: {e} — switching to {fallback}", flush=True)
        last_error = e

    for attempt in range(3):
        try:
            content = await _call_groq(fallback)
            print(f"[AGENT] Groq OK — model={fallback} attempt={attempt+1}", flush=True)
            return content, raw_comments
        except _RateLimitError as e:
            wait_sec = 20 * (attempt + 1)
            print(f"[AGENT] {fallback} rate limited (attempt {attempt+1}/3) — waiting {wait_sec}s", flush=True)
            last_error = e
            await asyncio.sleep(wait_sec)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            print(f"[AGENT] {fallback} network error attempt {attempt+1}: {e}", flush=True)
            last_error = e
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[AGENT] {fallback} failed attempt {attempt+1}: {e}", flush=True)
            last_error = e
            await asyncio.sleep(5)

    raise last_error or ValueError("Groq failed on all models")


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


async def fetch_comment_text_from_api(comment_id: str) -> str:
    """Fetch a single comment's text directly from the ClickUp API as a fallback."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{CLICKUP_BASE}/comment/{comment_id}",
                headers={"Authorization": CLICKUP_API_KEY}
            )
            if resp.status_code == 200:
                body = resp.json()
                text = extract_comment_text(body) or extract_comment_text(body.get("comment") or {})
                if text:
                    return text
    except Exception as e:
        print(f"[AGENT] fetch_comment_text_from_api({comment_id}) failed: {e}", flush=True)
    return ""


async def process_webhook(payload):
    event = payload.get("event")
    task_id = payload.get("task_id")
    history_items = payload.get("history_items", [])

    if not task_id or not event:
        return

    print(f"[AGENT] Webhook: event={event} task_id={task_id} history_items_count={len(history_items)}", flush=True)
    if history_items:
        # Log structure so we can debug comment text extraction misses
        import json
        sample = history_items[0]
        print(f"[AGENT] history_items[0] keys={list(sample.keys())}", flush=True)
        comment_preview = sample.get("comment") or (sample.get("data") or {}).get("comment") or {}
        print(f"[AGENT] comment_obj keys={list(comment_preview.keys()) if isinstance(comment_preview, dict) else type(comment_preview)}", flush=True)

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

                # If text extraction from webhook payload failed, try fetching via API
                if not raw_text:
                    comment_id = (comment_obj.get("id") if isinstance(comment_obj, dict) else None) or item.get("id") or ""
                    if comment_id:
                        print(f"[AGENT] Text extraction from payload failed — fetching comment {comment_id} from API", flush=True)
                        raw_text = await fetch_comment_text_from_api(comment_id)

                print(f"[AGENT] Bot-account comment text: {repr(raw_text[:120])}", flush=True)

                # Only skip if we have text AND it's NOT a short trigger.
                # If text is empty (extraction failed entirely), let it through —
                # determine_gate will check for trigger and gate=None → skip safely.
                if raw_text and not (_is_trigger(raw_text) and len(raw_text) <= 100):
                    print(f"[AGENT] Skipping — bot account comment (len={len(raw_text)})", flush=True)
                    return
                elif not raw_text:
                    print(f"[AGENT] Bot account comment — could not extract text, letting through to determine_gate", flush=True)

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
        err_type = type(e).__name__
        err_msg = str(e)[:300]
        print(f"[AGENT] evaluate_gate failed — {err_type}: {err_msg}", flush=True)
        traceback.print_exc()
        await post_comment(
            task_id,
            f"⚠️ SubInspector — {gate} gate check failed.\n`{err_type}: {err_msg}`",
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
