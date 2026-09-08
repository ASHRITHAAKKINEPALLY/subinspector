## **A DE ticket can no longer stay in Complete without its assignee's time on it**

_Time logging is now enforced at the moment of closure — if the assignee logged nothing, the ticket goes straight back to the status it came from._

Data Engineering, and the leads who plan capacity and report effort off logged time, now get one enforceable rule: a ticket is not closed until the person who did the work has said how long it took. Time on a DE ticket stops being a reporting afterthought and becomes a condition of closing it.

Until now, time logging was voluntary and retrospective. Whether a ticket carried its hours depended on who owned it and how the week had gone — some carried an entry per session, others closed clean with nothing on them. The cost is not visible on any single ticket, and it is not recoverable later: effort reconstructed a week after the fact is a guess, so every gap permanently weakens the numbers we use to size the next build, defend a timeline, or explain where a sprint went. A reminder does not fix this, because the moment the reminder matters is the moment the ticket is already closed and the context is gone.

SubInspector now runs a time-tracking check on every DE ticket that moves into Complete:

1. **Trigger** — a transition into Complete, and nothing else. Any other status change returns before a single API call is made.
2. **Whose time counts** — the assignee's. Anyone may log time against the ticket, but only the assignee's entries satisfy the rule.
3. **Threshold** — any non-zero amount. No minimum duration, no proof, no attachment, and no validation that the figure is accurate.
4. **On failure** — the ticket reopens to the status it held immediately before Complete, whatever that status was on that list, and SubInspector posts a **⏱ SubInspector DE Time Tracking Check** comment saying what is missing.
5. **On pass** — silence. A compliant ticket stays Complete and gets no comment, so the check adds no noise to the tickets that are already right.

The check is deliberately one-directional: it reopens a ticket only when it can prove nobody logged time, and stands down whenever it cannot. If the time-entry API is unavailable it falls back to the ticket's own time total, where zero is proof that nothing was logged and a non-zero total it cannot attribute is left alone. If the pre-Complete status cannot be determined, it does not revert — it logs and exits. A ticket with no assignee is left alone. A failed lookup never reopens a ticket on its own. Every ambiguous case resolves in favour of leaving the ticket where the human put it.

Scope is explicit and checkable rather than implied by the code: the gate is armed on the **Pulse Implementation Intake** folder and the **iQ Enterprise ClickUp Space**, and SubInspector's `/health` endpoint echoes the exact folder and space IDs it is enforcing. Adding a scope is configuration, not a build — with one precondition, that the lists in it actually carry a status named `complete`, since status sets differ from list to list.

This runs as an independent track ahead of the existing quality gates. INTAKE, PRE-EXECUTION and CLOSURE scoring is untouched — the feature landed as additions only, with no lines removed from the gate logic. It is live now on both scopes and verified end to end on the pass path, the reopen path, and the fail-open paths.

### Internal FAQs

**Why now?**
Every effort estimate, capacity plan and delivery post-mortem is built on logged time, and the data is only as good as the least disciplined ticket. The gap is silent — nothing breaks, the numbers just quietly stop being true. Enforcing at closure is the last moment the information still exists in someone's head, so it is the only cheap place to catch it.

**Why this approach — a gate at closure rather than a reminder?**
Reminders were already the informal standard and produced inconsistent logging, because the incentive at the moment of closing a ticket is to close it. A gate puts the cost on the person who has the context, at the moment they have it, and makes the standard improvable in one place instead of one person at a time. It also means compliance is a fact about the ticket rather than a claim about the team.

**What's the biggest risk?**
Token logging. A rule that accepts any non-zero amount can be satisfied with one minute, and a ticket that is technically compliant but carries a meaningless figure is worse than a blank one, because it looks like data. The gate does not judge the number, and it is not intended to — the counterweight is the lead reviewing effort against the work, exactly as before.

**Does this create extra work for DE?**
No new steps, and one fewer thing to chase. If time is logged as the work happens, the check is invisible — it posts nothing and changes nothing. It is only felt on a ticket being closed with no time on it at all, which is the case it exists for.

**Why no minimum duration and no accuracy check?**
Because both would need a defensible number, and we do not have one yet. A threshold picked arbitrarily would either be trivially passable or start rejecting legitimate short tickets, and either outcome damages trust in the gate faster than the loose rule damages the data. Presence first; calibration once there is enough logged history to argue from.

**What are we explicitly not doing?**
We are not touching the quality gates, changing what DE builds, or changing who can close a ticket. We are not requiring proof, attachments, or a specific tracking method. We are not applying the check retroactively to tickets already closed. And we are not extending it beyond the two configured scopes — that is a deliberate, per-scope decision, not a default.
