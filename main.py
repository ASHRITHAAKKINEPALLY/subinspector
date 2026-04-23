from fastapi import FastAPI, Request, BackgroundTasks
from contextlib import asynccontextmanager
from agent import process_webhook
import traceback
import asyncio
import httpx

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
    asyncio.create_task(keep_alive())
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

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "SubInspector is running"}
