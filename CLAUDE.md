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

**DE Tickets (7 required sections):**

All 7 sections must be present, substantive, and complete. Any section missing, empty, or containing placeholder text (TBD, "to be determined", "will update", "TBA") = FAIL.

| # | Section | What I must include |
|---|---------|---|
| 1 | Problem Statement | What problem or limitation are we addressing? What's currently missing, inefficient, inconsistent, or error-prone? Why is this important now? Must be concrete and specific — never just restate the title. |
| 2 | Objective | What exactly needs to be done? Mention specific models, logic, fields, or automation being built or changed. Is it a fix, enhancement, or new feature? Must be clear and concrete. |
| 3 | Impact | Why is this change valuable? Who benefits (analysts, dashboards, QA, other systems)? How does it improve data trust, accuracy, efficiency, or reduce manual work? |
| 4 | Acceptance Criteria | When is this done? List 2–3 clear, observable outcomes that confirm completion (e.g., "bot runs without errors", "mismatch % reported", "no schema breaks"). Must be measurable, not vague. |
| 5 | Notes / Risks | Any SQL logic, edge cases, known risks, or dependencies? Mention affected models, config blocks, impacted reports, data quality concerns. For complex work, this section must be filled — "none identified" without deeper investigation = FAIL. |
| 6 | Solution Approach | How will this be solved? Summarize proposed steps, tools (SQL, dbt, RPA, scripts), fallback logic, or automation flow. Must be actionable and guide development. |
| 7 | RCA (Root Cause Analysis) | Why is this issue happening? Describe the root cause — sync delays, transformation gaps, manual errors, config problems, schema misalignment. Understanding the root prevents recurrence. |

**BI tickets (title has [BI] or references Tableau/Power BI/dashboard):**
1. Problem statement names dashboard + target persona + business value
2. BI tool explicitly named (Tableau / Power BI) + workspace/publish destination
3. Full BQ path confirmed (project.dataset.table)
4. KPIs/metrics defined with calculation logic or BRD reference
5. Definition of Done — what the finished dashboard shows + how sign-off is given
6. Screenshot / mockup / wireframe — PASS if mockup, wireframe, sample layout, or reference screenshot of a similar report is attached or described. FAIL only if there is zero visual reference or output format description.

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

**INTAKE Gate (DE Tickets) — 7 sections required:**
- Problem Statement that just restates title → must describe the actual problem, inefficiency, or gap
- Objective that is vague ("improve it") → must name specific deliverables (models, logic, fields, automations)
- Impact that is speculative ("should help") → must name who benefits and how (accuracy %, time saved, manual work eliminated)
- Acceptance Criteria that is unmeasurable ("complete the work") → must list 2–3 observable outcomes (query runs, metric reported, no breaks)
- Notes/Risks section empty or "none identified" → for complex work, must discuss edge cases, affected models, data quality concerns
- Solution Approach that is vague ("figure it out") → must outline steps, tools (SQL, dbt, scripts), and fallback logic
- RCA section missing or "cause unclear" → must explain root cause (sync delay, transformation gap, manual error, config issue)
- Any section with TBD / "to be determined" / "will update" / placeholder text → every section must be substantive on submission
- Any section heading with no content under it → fill every heading with concrete details

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
