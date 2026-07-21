#!/usr/bin/env python3
"""
Delete all SubInspector comments posted today (2026-07-21).
Run this via: python3 cleanup_spam.py
"""
import os
import asyncio
import httpx
from datetime import datetime

CLICKUP_API_KEY = os.environ.get("CLICKUP_API_KEY")
BOT_USER_ID = os.environ.get("BOT_USER_ID", "100965864")
ENFORCEMENT_FOLDERS = os.environ.get("ENFORCEMENT_FOLDERS", "90165998786").split(",")

if not CLICKUP_API_KEY:
    print("ERROR: CLICKUP_API_KEY not set in environment")
    exit(1)

async def get_folder_tasks(folder_id):
    """Get all tasks in a folder."""
    async with httpx.AsyncClient() as client:
        url = f"https://api.clickup.com/api/v2/folder/{folder_id}/task"
        resp = await client.get(url, headers={"Authorization": CLICKUP_API_KEY}, params={"limit": 1000})
        if resp.status_code == 200:
            return resp.json().get("tasks", [])
        return []

async def delete_comment(comment_id):
    """Delete a single comment."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"https://api.clickup.com/api/v2/comment/{comment_id}",
            headers={"Authorization": CLICKUP_API_KEY}
        )
        return resp.status_code in [200, 204]

async def delete_bot_comments_from_today():
    """Find and delete all bot comments from today across enforcement folders."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    deleted_count = 0

    print(f"[CLEANUP] Starting deletion of SubInspector comments from {today_str}...")
    print(f"[CLEANUP] Searching folders: {ENFORCEMENT_FOLDERS}")

    for folder_id in ENFORCEMENT_FOLDERS:
        print(f"\n[CLEANUP] Fetching tasks from folder {folder_id}...")
        tasks = await get_folder_tasks(folder_id)
        print(f"[CLEANUP] Found {len(tasks)} tasks in folder {folder_id}")

        for task in tasks:
            task_id = task.get("id")
            if not task_id:
                continue

            # Get all comments for this task
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.clickup.com/api/v2/task/{task_id}/comments",
                    headers={"Authorization": CLICKUP_API_KEY}
                )

                if resp.status_code != 200:
                    continue

                comments = resp.json().get("comments", [])

                for comment in comments:
                    user_id = str((comment.get("user") or {}).get("id", ""))
                    date_created = comment.get("date", "")

                    # Check if from bot and from today
                    if user_id == BOT_USER_ID and date_created.startswith(today_str):
                        comment_id = comment.get("id")
                        success = await delete_comment(comment_id)
                        if success:
                            print(f"  ✓ Deleted comment {comment_id} from task {task_id}")
                            deleted_count += 1
                        else:
                            print(f"  ✗ Failed to delete comment {comment_id}")

    print(f"\n[CLEANUP] Done. Deleted {deleted_count} comments total.")
    return deleted_count

if __name__ == "__main__":
    asyncio.run(delete_bot_comments_from_today())
