from fastapi import FastAPI, Request, BackgroundTasks, Query
from contextlib import asynccontextmanager
from agent import process_webhook, scan_and_backfill, CLICKUP_API_KEY, ENFORCEMENT_FOLDERS, _build_advisory_space_map
import traceback
import asyncio
import os
import httpx

CLICKUP_TEAM_ID  = os.environ.get("CLICKUP_TEAM_ID", "3369097")
WEBHOOK_ENDPOINT = os.environ.get("WEBHOOK_ENDPOINT", "https://ashakkinepally-subinspector.hf.space/webhook")
WEBHOOK_EVENTS   = ["taskCreated", "taskStatusUpdated", "taskCommentPosted"]


async def ensure_webhook():
    """On startup, check the ClickUp webhook is active. Recreate it if suspended or missing."""
    await asyncio.sleep(5)  # let the server finish binding first
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.clickup.com/api/v2/team/{CLICKUP_TEAM_ID}/webhook",
                headers={"Authorization": CLICKUP_API_KEY}
            )
            hooks = resp.json().get("webhooks", [])

        # Find our webhook by endpoint URL
        our_hooks = [h for h in hooks if h.get("endpoint") == WEBHOOK_ENDPOINT]

        # Check if any of them is healthy
        healthy = [h for h in our_hooks if (h.get("health") or {}).get("status") == "active"]

        if healthy:
            print(f"[WEBHOOK] Startup check — webhook active (id={healthy[0]['id']})", flush=True)
            return

        # Delete any suspended/broken copies pointing to our URL
        for h in our_hooks:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.delete(
                    f"https://api.clickup.com/api/v2/webhook/{h['id']}",
                    headers={"Authorization": CLICKUP_API_KEY}
                )
            print(f"[WEBHOOK] Deleted suspended webhook {h['id']}", flush=True)

        # Create a fresh one
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://api.clickup.com/api/v2/team/{CLICKUP_TEAM_ID}/webhook",
                headers={"Authorization": CLICKUP_API_KEY, "Content-Type": "application/json"},
                json={"endpoint": WEBHOOK_ENDPOINT, "events": WEBHOOK_EVENTS}
            )
            new_hook = r.json().get("webhook", r.json())
        print(f"[WEBHOOK] Recreated webhook — id={new_hook.get('id')} health={new_hook.get('health',{}).get('status')}", flush=True)

    except Exception as e:
        print(f"[WEBHOOK] ensure_webhook failed: {e}", flush=True)


async def keep_alive():
    """Ping own health endpoint every 4 minutes to prevent HF Space from sleeping."""
    await asyncio.sleep(60)  # wait for app to fully start first
    while True:
        try:
            async with httpx.AsyncClient() as client:
                await client.get("http://localhost:7860/health", timeout=10)
            print("[KEEPALIVE] ping ok", flush=True)
        except Exception:
            pass
        await asyncio.sleep(240)  # 4 minutes

@asynccontextmanager
async def lifespan(app):
    asyncio.create_task(ensure_webhook())
    asyncio.create_task(keep_alive())
    asyncio.create_task(_build_advisory_space_map())
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    print(f"[WEBHOOK] Event: {payload.get('event')} | Task: {payload.get('task_id')}", flush=True)
    background_tasks.add_task(run_webhook, payload)
    return {"status": "received"}

async def run_webhook(payload):
    try:
        await process_webhook(payload)
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        traceback.print_exc()

@app.post("/scan")
async def scan_tickets(
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(default=False, description="Set true to identify missed tickets without posting comments"),
    folder_id: str = Query(default=None, description="ClickUp folder ID to scan (defaults to IH enforcement folder)")
):
    """
    Scan all tasks in the IH folder and post gate comments for any that were missed.
    Missed = task has no SubInspector comment matching its current gate.
    No status reverts — backfill is comment-only.
    """
    target = folder_id or ENFORCEMENT_FOLDERS[0]
    background_tasks.add_task(run_scan, target, dry_run)
    return {"status": "scan started", "folder_id": target, "dry_run": dry_run}


async def run_scan(folder_id: str, dry_run: bool):
    try:
        results = await scan_and_backfill(folder_id, dry_run=dry_run)
        print(
            f"[SCAN] Complete — scanned={results['scanned']} "
            f"missed={results['missed']} posted={results['posted']} "
            f"errors={results['errors']}",
            flush=True
        )
    except Exception as e:
        print(f"[SCAN ERROR] {e}", flush=True)
        traceback.print_exc()


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "SubInspector is running"}
