# SubInspector — Claude Working Rules

## 1. ClickUp Comment Formatting — ALWAYS USE RICH TEXT BLOCKS

**NEVER post plain `comment_text` string to ClickUp API.**
Always use the `comment` array of block objects. This renders bold headers, bullets, and proper spacing.

### Format to use every time:
```python
payload = {
  "comment": [
    {"text": "Header text here", "attributes": {"bold": True}},
    {"text": "\n\nBody paragraph text.\n\n"},
    {"text": "Section title\n", "attributes": {"bold": True}},
    {"text": "  -  Bullet point one\n"},
    {"text": "  -  Bullet point two\n\n"},
    {"text": "Label: ", "attributes": {"bold": True}},
    {"text": "inline value text"}
  ]
}
```

### Rules:
- Section headers → `{"text": "...", "attributes": {"bold": True}}`
- Body text / bullets → `{"text": "...", "attributes": {}}` (no attributes key needed)
- Blank line between sections → `{"text": "\n\n"}`
- Bullets → `  -  bullet text\n` (two spaces, dash, two spaces)
- Never use markdown syntax (`**bold**`, `# heading`) — ClickUp ignores it in API posts
- Always post via Python `urllib.request` or `httpx`, never via raw bash curl with special chars

---

## 2. SubInspector Gate Checklists — First-Pass Rules

**Any ticket I write or help write for Instant Hydration must pass all relevant gates on first try.**
Apply the checklist for the gate that will fire next based on the ticket's current status.

---

### GATE 1 — INTAKE (fires on ticket creation)

| # | Check | What I must include |
|---|---|---|
| 1 | Problem Statement | User-story format: "As a [persona] at IH, I want [capability], so that [value]." Must name the metric/area + a value-realization signal. Never just restate the title. |
| 2 | Steps to Reproduce | Explicit nav path + filter/date range + which view/report + which number to look at. Followable by a new person without a meeting. |
| 3 | Definition of Done | Observable end state — what artifact/output will change and exactly how to confirm it is complete. Never "fix it" or "update logic". |
| 4 | Screenshots / Evidence | Attach or link proof: screenshot, export, query result, or before/after numbers. Required whenever the claim depends on UI or output differences. |
| 5 | Mandatory Fields | All required fields present and non-empty. Description must have substantive content under each heading, not just the heading itself. |
| 6 | DE Actionability | Full BigQuery path (project.dataset.table) where data work is involved. No TBD language. All dependencies recorded. Actionable without a clarifying meeting. |

**BI tickets (title has [BI] or references Tableau/Power BI/dashboard):**
1. Problem statement names dashboard + target persona + business value
2. BI tool explicitly named (Tableau / Power BI) + workspace/publish destination
3. Full BQ path confirmed (project.dataset.table)
4. KPIs/metrics defined with calculation logic or BRD reference
5. Definition of Done — what the finished dashboard shows + how sign-off is given
6. Screenshot / mockup / wireframe attached

---

### GATE 2 — PRE-EXECUTION (fires when status → ready / in progress / development / code-review)

| # | Check | What must be present |
|---|---|---|
| 1 | BA Inputs Complete | All 6 BA Inputs present and complete: (1) problem statement, (2) expected output, (3) scope/edge cases + timeline, (4) validation checks, (5) success criteria, (6) data source + business context. None missing, none TBD. |
| 2 | Valid DE Assignee | At least one DE person assigned. Komal Saraogi and Frido = BA/mgmt, do NOT count. Anudeep counts only for BI tickets. |
| 3 | Data Source Confirmed | Full BigQuery path: `project.dataset.table`. "In BQ" or "the normal table" = FAIL. |
| 4 | Feasibility Assessment | For T2/T3: a technical review comment must exist. T1 = auto-pass. |
| 5 | Dependencies Unblocked | All dependencies listed with owners. Each resolved or explicitly marked N/A with rationale. |
| 6 | Scope Locked | Zero TBD / "to be decided" / "figure out" / TBA language in any execution-critical part of the description. |

---

### GATE 3 — CLOSURE (fires when status → qa / uat / prod-review / complete / done)

| # | Check | What must be present |
|---|---|---|
| 1 | Acceptance Criteria Addressed | Every criterion confirmed complete in a comment or checklist. A "Moving to Done" or completion comment with 🎉 or ✅ auto-passes this. |
| 2 | Evidence Attached | Screenshots, query results, validation sheet links, or before/after outputs. Google Sheets / Docs / GitHub PR links in comments count. |
| 3 | QA Sign-Off | Ashritha Akkinepally's closure notes = auto-pass. Any non-assignee confirming completion counts. |
| 4 | No Open Subtasks | All subtasks closed or marked N/A. |
| 5 | Stakeholder Notified | Any team member comment confirming work is done counts. No explicit @mention required. |
| 6 | Documentation Updated | Bug fixes / logic updates / config changes → auto-pass (N/A implied). New features/dashboards need explicit confirmation or N/A note. |

---

## 3. Tier Classification

| Tier | When to use |
|---|---|
| T1 | Label fix, filter change, config tweak. Light gate — description + success criteria sufficient. Feasibility auto-passes. |
| T2 | Analysis, moderate modeling, enhancement. All 6 BA Inputs required. |
| T3 | Title/description contains dashboard/dataset/model/client/logic/allocation + new build. Full gate, any TBD = FAIL. |

**Override:** If tier is wrong, comment `/si check Tier: T1` (or T2/T3) to force re-evaluation at correct tier without triggering a revert.

---

## 4. Key People Rules

- **Komal Saraogi** — PM/BA only. Does NOT count as DE assignee.
- **Frido (Fridolin Steffe Mijo)** — Management. Does NOT count as DE assignee.
- **Anudeep** — Valid DE assignee for BI tickets only.
- **Ashritha Akkinepally** — Team lead. Her closure notes = QA sign-off auto-pass.

---

## 5. Common FAIL Triggers to Avoid

- Problem statement that just restates the title → rewrite in user-story format
- "in BQ" or "the normal table" instead of full path → always write `project.dataset.table`
- Definition of Done that says "fix it" or "update the logic" → name the artifact and how to verify
- Any section heading with no content under it → fill every heading
- TBD / N/A to fill later / will update → never leave these in an active ticket
- No evidence attached when claim is about UI/output differences → attach screenshot or link

---

## 6. Repo & Deployment Reference

| Item | Value |
|---|---|
| Local git | `C:\Users\Ashritha Akkinepally\SubInspector\` |
| GitHub | `https://github.com/ASHRITHAAKKINEPALLY/subinspector` |
| HF Space | `https://huggingface.co/spaces/ashakkinepally/subinspector` |
| HF Logs | `https://huggingface.co/spaces/ashakkinepally/subinspector?logs=container` |
| Push both remotes | `git push origin main && git push hf main` |
| Enforcement folder | `90165998786` (Instant Hydration) |
| Bot account ID | `100965864` |
