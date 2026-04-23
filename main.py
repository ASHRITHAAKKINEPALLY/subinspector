from fastapi import FastAPI, Request, BackgroundTasks
from agent import process_webhook
import traceback

app = FastAPI()

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    print(f"[WEBHOOK] Event: {payload.get('event')} | Task: {payload.get('task_id')}", flush=True)
    background_tasks.add_task(run_webhook, payload)
    return {"status": "received"}

async def run_webhook(payload):
    try:
        import json
        print(f"[PAYLOAD] {json.dumps(payload)}", flush=True)
        await process_webhook(payload)
    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        traceback.print_exc()

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "SubInspector is running"}
