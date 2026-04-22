# SubInspector

Credit-independent ClickUp ticket quality gate enforcer. Powered by Groq (free) + FastAPI.

## Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/new)

## Setup (2 steps)

### 1. Set environment variables in Railway
| Variable | Value |
|----------|-------|
| `GROQ_API_KEY` | Your Groq API key from console.groq.com |
| `CLICKUP_API_KEY` | Your ClickUp API key |
| `ENFORCEMENT_FOLDERS` | Comma-separated folder IDs (default: `90165998786`) |

### 2. Register the ClickUp webhook
Replace `YOUR_RAILWAY_URL` with your Railway app URL:

```
POST https://api.clickup.com/api/v2/team/{team_id}/webhook
{
  "endpoint": "https://YOUR_RAILWAY_URL/webhook",
  "events": ["taskCreated", "taskStatusUpdated", "taskCommentPosted"]
}
```

## Usage
- Post `/si check` on any ticket for a dry run gate check
- Status changes auto-trigger the gate check
- SubInspector posts the result as a comment on the ticket
