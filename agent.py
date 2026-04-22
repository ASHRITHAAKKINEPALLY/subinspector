import os
import re
import httpx

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CLICKUP_API_KEY = os.environ.get("CLICKUP_API_KEY")
ENFORCEMENT_FOLDERS = os.environ.get("ENFORCEMENT_FOLDERS", "90165998786").split(",")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CLICKUP_BASE = "https://api.clickup.com/api/v2"

PRE_EXEC_STATUSES = ["backlog", "ready", "in progress", "in progess", "development", "code-review", "code review"]
CLOSURE_STATUSES = ["qa", "uat", "prod review", "prod-review", "complete", "done", "ready to close"]

SYSTEM_PROMPT = """You are SubInspector, a strict ClickUp ticket quality gate enforcer.

INTAKE GATE (6 checks):
1. Problem Statement (user-story format: As a… I want… so that…)
2. Steps to Reproduce
3. Definition of Done
4. Screenshots/Evidence
5. Mandatory Fields not empty
6. DE Actionability (data source confirmed, no clarifying meeting needed)

PRE-EXECUTION GATE (6 checks):
1. BA Inputs Complete (all 6 present)
2. Valid DE Assignee (not Komal or Frido)
3. Data Source Confirmed (full BigQuery project.dataset.table path)
4. Feasibility Assessment (required for T2/T3)
5. Dependencies Identified and Unblocked
6. Scope Locked (no TBD/placeholder language)

CLOSURE GATE (6 checks):
1. All Acceptance Criteria Addressed
2. Evidence Attached (screenshots, query results, before/after)
3. QA Sign-Off Present
4. No Open Subtasks or Blockers
5. Stakeholder Notified
6. Documentation Updated (or explicit N/A)

Pass = 6/6. Below 6/6 = FAIL.
Respond ONLY in this format:
RESULT: PASS or FAIL
SCORE: X/6
CHECKS:
| # | Check | Result | Detail |
|---|-------|--------|--------|
SUMMARY: one sentence."""


def determine_gate(event, status, history_items):
    status = status.lower()

    if event == "taskCreated":
        return "INTAKE", False

    elif event == "taskStatusUpdated":
        new_status = ""
        if history_items:
            new_status = (history_items[0].get("after", {}) or {}).get("status", "") or ""
        new_status = new_status.lower()
        if any(s in new_status for s in PRE_EXEC_STATUSES):
            return "PRE-EXECUTION", False
        elif any(s in new_status for s in CLOSURE_STATUSES):
            return "CLOSURE", False

    elif event == "taskCommentPosted":
        comment_text = ""
        if history_items:
            comment = history_items[0].get("comment", {}) or {}
            comment_items = comment.get("comment", []) or []
            if comment_items:
                comment_text = comment_items[0].get("text", "") or ""
            if not comment_text:
                comment_text = comment.get("text_content", "") or ""
        comment_text = comment_text.lower()

        if "/si check" in comment_text or "/subinspector check" in comment_text:
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
            headers={"Authorization": CLICKUP_API_KEY}
        )
        return response.json()


async def evaluate_gate(gate, task):
    description = (task.get("description") or "")[:3000]
    assignees = ", ".join(a.get("username", "") for a in task.get("assignees", [])) or "None"

    user_message = (
        f"Gate: {gate}\n"
        f"Task: {task.get('name', '')}\n"
        f"Status: {task.get('status', {}).get('status', '')}\n"
        f"Assignees: {assignees}\n"
        f"Description:\n{description}"
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
                "max_tokens": 1000,
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
    in_scope = folder_id in ENFORCEMENT_FOLDERS

    status = (task.get("status") or {}).get("status", "")
    previous_status = ""
    if history_items:
        before = history_items[0].get("before") or {}
        previous_status = before.get("status", "") if isinstance(before, dict) else ""
    previous_status = previous_status or "backlog"

    gate, is_dry_run = determine_gate(event, status, history_items)

    if not gate:
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
        comment = f"🔁 {gate} Gate Failed — {score}/6 checks passed. Minimum required: 6/6.\n\n{content}"
        if in_scope:
            comment += f"\n\nTicket reverted to: {previous_status}\n@Komal Saraogi — please review."

    await post_comment(task_id, comment)

    if not passed and not is_dry_run and in_scope:
        await revert_status(task_id, previous_status)
