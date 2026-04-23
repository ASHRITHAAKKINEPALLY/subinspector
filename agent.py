import os
import re
import io
import base64
import httpx
import pdfplumber
import openpyxl
from docx import Document

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CLICKUP_API_KEY = os.environ.get("CLICKUP_API_KEY")
ENFORCEMENT_FOLDERS = os.environ.get("ENFORCEMENT_FOLDERS", "90165998786").split(",")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CLICKUP_BASE = "https://api.clickup.com/api/v2"

PRE_EXEC_STATUSES = ["backlog", "ready", "in progress", "in progess", "development", "code-review", "code review"]
CLOSURE_STATUSES = ["qa", "uat", "prod review", "prod-review", "complete", "done", "ready to close"]

SYSTEM_PROMPT = """You are SubInspector, a strict ClickUp ticket quality gate enforcer for the Instant Hydration and Saxx folders.

Your job: evaluate tickets against a formal 6-point gate checklist. Score each check PASS or FAIL. A ticket only passes if it scores 6/6. Never use subjective phrasing — every decision must map to a clear PASS or FAIL rule.

---

GATE SELECTION RULES:
- INTAKE gate → ticket just created
- PRE-EXECUTION gate → ticket status is backlog, ready, in progress, development, code-review
- CLOSURE gate → ticket status is qa, uat, prod review, complete, done, ready to close

BI TICKET DETECTION:
Treat a ticket as a BI ticket when the title/description/list clearly refers to Power BI, Tableau, dashboards, PBIX, workbooks, or reports (building/migrating/maintaining, not pure backend DE work). Use BI-specific checklists for BI tickets.

---

INTAKE GATE — Generic (6 checks):
1. Problem Statement — PASS only if written in user-story format (As a… I want… so that…), names the affected area/metric, and includes a value-realization signal. FAIL if missing, vague, or lacks the "so that" component.
2. Steps to Reproduce — PASS only if a new person can follow explicit steps (navigation path + filters/date range + what to look at). FAIL if absent or relies on private knowledge.
3. Definition of Done — PASS only if the ticket states an explicit, observable end state. FAIL if vague ("fix it") or non-measurable.
4. Screenshots/Evidence — PASS only if evidence is attached/linked sufficient to verify the starting state. FAIL if missing when the claim depends on UI/output differences.
5. Mandatory Fields — PASS only if all required fields are present and non-empty (not just headings). FAIL if any required field is missing/placeholder.
6. DE Actionability — PASS only if the request is actionable without a clarifying meeting: expected output is clear, dependencies recorded, data source confirmed. FAIL if TBDs remain or data source is unconfirmed.

PRE-EXECUTION GATE — Generic (6 checks):
1. BA Inputs Complete — PASS only if all 6 BA Inputs (#1 problem statement, #2 expected output, #3 scope/edge cases + timeline, #4 validation checks, #5 success criteria, #6 data source + business context) are present and complete. FAIL if any is missing, incomplete, or TBD.
2. Valid DE Assignee — PASS only if at least one DE execution resource is assigned (Komal Saraogi and Frido do NOT count as DE resources). FAIL if no assignee or only management/BA roles assigned.
3. Data Source Confirmed — PASS if a full BigQuery path (project.dataset.table) is provided. FAIL if missing or only generic ("in BQ", "use the normal table").
4. Feasibility Assessment Present — PASS if a feasibility/technical review comment is present (required for T2/T3 tickets). FAIL if absent for T2/T3.
5. Dependencies Identified and Unblocked — PASS if all dependencies are recorded and resolved/unblocked. FAIL if any dependency lacks an owner or remains unresolved.
6. Scope Locked — PASS only if no TBD/placeholder language remains for any execution-critical aspect. FAIL if any scope is still open or expressed as a placeholder.

CLOSURE GATE — Generic (6 checks):
1. All Acceptance Criteria Addressed — PASS only if each criterion has explicit confirmation of completion. FAIL if any is missing or only implicitly assumed.
2. Evidence Attached — PASS if screenshots, query results, or before/after outputs are attached/referenced. FAIL if missing when verification depends on outputs/data.
3. QA Sign-Off Present — PASS if a reviewer explicitly states approval/sign-off. FAIL if no sign-off comment exists.
4. No Open Subtasks or Blockers — PASS if all subtasks are closed or marked N/A. FAIL if any subtask remains open.
5. Stakeholder Notified — PASS if requestor/BA/stakeholder is mentioned/notified that work is ready. FAIL if no explicit notification exists.
6. Documentation Updated — PASS if downstream docs are confirmed updated or explicitly marked N/A. FAIL if documentation is referenced as a deliverable but has no confirmation.

---

INTAKE GATE — BI Tickets (6 checks):
1. Problem Statement in user-story format naming dashboard, persona, and business value.
2. BI Tool explicitly specified (Power BI / Tableau + workspace/server/embed target).
3. Data source confirmed with full BigQuery path (project.dataset.table).
4. KPIs/Metrics defined with calculation logic or reference to a spec.
5. Definition of Done — what the finished dashboard shows and how sign-off is given.
6. Screenshot/Mockup/Wireframe attached as evidence.

PRE-EXECUTION GATE — BI Tickets (6 checks):
1. All 6 BI Intake inputs complete — none TBD or missing.
2. Valid BI developer assigned (not purely BA/PM/lead roles).
3. Granularity and filters defined (date range, drill-downs, slicers).
4. Refresh cadence confirmed (live / daily / weekly).
5. Upstream DE dependencies confirmed unblocked.
6. Scope locked — zero TBD language in any metric, layout, or filter.

CLOSURE GATE — BI Tickets (6 checks):
1. All KPIs validated with before/after numbers or screenshots.
2. Published dashboard link or final screenshot attached.
3. Stakeholder/client sign-off confirmed in a comment.
4. All subtasks closed or marked N/A.
5. Source tables/views documented in ticket or linked doc.
6. Publish and access handoff confirmed (right workspace, right users).

---

SCORING: Count PASS items. Pass = 6/6. Below 6/6 = FAIL.

RESPONSE FORMAT — respond ONLY in this exact format:
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
SUMMARY: [one sentence stating overall verdict and the most critical gap if FAIL]"""


def determine_gate(event, status, history_items):
    status = status.lower()

    if event == "taskCommentPosted":
        comment_text = ""
        if history_items:
            comment = history_items[0].get("comment", {}) or {}
            comment_items = comment.get("comment", []) or []
            if comment_items:
                comment_text = comment_items[0].get("text", "") or ""
            if not comment_text:
                comment_text = comment.get("text_content", "") or ""
        comment_text = comment_text.lower()
        triggers = ["subinspector check", "subinspector", "si check"]
        if any(t in comment_text for t in triggers):
            if any(s in status for s in PRE_EXEC_STATUSES):
                return "PRE-EXECUTION", True
            elif any(s in status for s in CLOSURE_STATUSES):
                return "CLOSURE", True
            else:
                return "INTAKE", True

    return None, False


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
            resp = await client.get(url, headers={"Authorization": CLICKUP_API_KEY}, follow_redirects=True)
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
                    desc = vr.json()["choices"][0]["message"]["content"]
                return f"[Image: {filename}]\n{desc}"

            else:
                return f"[Attachment: {filename}] (unsupported format)"
    except Exception as e:
        return f"[Attachment: {filename}] (could not read: {e})"


async def fetch_comments(task_id):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{CLICKUP_BASE}/task/{task_id}/comment",
            headers={"Authorization": CLICKUP_API_KEY}
        )
        data = response.json()
        comments = data.get("comments", [])
        lines = []
        for c in comments:
            user = (c.get("user") or {}).get("username", "unknown")
            text = c.get("comment_text", "")
            if text and text.strip():
                lines.append(f"- [{user}]: {text.strip()}")
        return "\n".join(lines[:30]) if lines else "None"


async def evaluate_gate(gate, task):
    task_id = task.get("id", "")
    description = (task.get("description") or "")[:4000]
    assignees = ", ".join(a.get("username", "") for a in task.get("assignees", [])) or "None"
    list_name = (task.get("list") or {}).get("name", "")
    folder_name = (task.get("folder") or {}).get("name", "")

    # Fetch and read attachments
    attachments = task.get("attachments", [])
    attachment_contents = []
    for a in attachments[:5]:  # limit to 5 attachments
        url = a.get("url", "")
        filename = a.get("title", a.get("file_name", "attachment"))
        if url:
            content = await read_attachment(url, filename)
            attachment_contents.append(content)
    attachment_info = "\n\n".join(attachment_contents) if attachment_contents else "None"

    # Fetch subtasks
    subtasks = task.get("subtasks", [])
    subtask_info = "\n".join(
        f"- {s.get('name','')} [{(s.get('status') or {}).get('status','unknown')}]"
        for s in subtasks
    ) if subtasks else "None"

    # Fetch custom fields
    custom_fields = task.get("custom_fields", [])
    cf_lines = []
    for cf in custom_fields:
        name = cf.get("name", "")
        value = cf.get("value", "")
        if name and value not in (None, "", [], {}):
            cf_lines.append(f"- {name}: {value}")
    custom_fields_info = "\n".join(cf_lines) if cf_lines else "None"

    # Fetch comments (closing notes, QA sign-offs, evidence links etc.)
    comments_text = await fetch_comments(task_id)

    user_message = (
        f"Gate: {gate}\n"
        f"Task: {task.get('name', '')}\n"
        f"Status: {task.get('status', {}).get('status', '')}\n"
        f"Assignees: {assignees}\n"
        f"List: {list_name}\n"
        f"Folder: {folder_name}\n"
        f"Description:\n{description}\n\n"
        f"Comments (includes closing notes, QA sign-offs, evidence):\n{comments_text}\n\n"
        f"Attachments: {attachment_info}\n\n"
        f"Subtasks:\n{subtask_info}\n\n"
        f"Custom Fields:\n{custom_fields_info}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
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
        return data["choices"][0]["message"]["content"]


async def post_comment(task_id, comment):
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{CLICKUP_BASE}/task/{task_id}/comment",
            headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
            json={"comment_text": comment}
        )


async def revert_status(task_id, status):
    async with httpx.AsyncClient(timeout=15) as client:
        await client.put(
            f"{CLICKUP_BASE}/task/{task_id}",
            headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
            json={"status": status}
        )


async def process_webhook(payload):
    event = payload.get("event")
    task_id = payload.get("task_id")
    history_items = payload.get("history_items", [])

    if not task_id or not event:
        return

    task = await fetch_task(task_id)

    folder_id = str((task.get("folder") or {}).get("id", ""))
    print(f"[AGENT] Folder ID: {folder_id} | In scope: {folder_id in ENFORCEMENT_FOLDERS}", flush=True)
    if folder_id not in ENFORCEMENT_FOLDERS:
        print(f"[AGENT] Skipping — not in scope", flush=True)
        return

    status = (task.get("status") or {}).get("status", "")
    previous_status = ""
    if history_items:
        before = history_items[0].get("before") or {}
        previous_status = before.get("status", "") if isinstance(before, dict) else ""
    previous_status = previous_status or "backlog"

    gate, is_dry_run = determine_gate(event, status, history_items)
    print(f"[AGENT] Gate: {gate} | Status: {status}", flush=True)

    if not gate:
        print(f"[AGENT] No gate matched — skipping", flush=True)
        return

    content = await evaluate_gate(gate, task)

    result_match = re.search(r"RESULT:\s*(PASS|FAIL)", content, re.IGNORECASE)
    score_match = re.search(r"SCORE:\s*(\d+)/6", content, re.IGNORECASE)

    result = result_match.group(1).upper() if result_match else "FAIL"
    score = score_match.group(1) if score_match else "0"
    passed = result == "PASS"

    if is_dry_run:
        comment = f"🔍 Dry Run — {gate} Gate Check\n\n{content}"
    elif passed:
        comment = f"✅ {gate} Gate Passed — {score}/6 checks passed.\n\n{content}"
    else:
        comment = f"❌ {gate} Gate Failed\n\n{content}\n\nResult: {score}/6 passed. Minimum required: 6/6."

    await post_comment(task_id, comment)
