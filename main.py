from fastapi import FastAPI, Request, BackgroundTasks
from agent import process_webhook

app = FastAPI()

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    background_tasks.add_task(process_webhook, payload)
    return {"status": "received"}

@app.get("/health")
async def health():
    return {"status": "ok"}
