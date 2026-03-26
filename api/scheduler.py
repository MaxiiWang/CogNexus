"""
APScheduler integration for CogNexus agent tasks
"""
import json
import uuid
import asyncio
import traceback
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import get_db

scheduler = AsyncIOScheduler()


def parse_cron(expr: str) -> dict:
    """Parse '0 8 * * *' into CronTrigger kwargs"""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expr}")
    return {
        'minute': parts[0],
        'hour': parts[1],
        'day': parts[2],
        'month': parts[3],
        'day_of_week': parts[4],
    }


async def execute_task(task_id: str):
    """Unified task execution entry point"""
    from task_runners import get_runner

    conn = get_db()
    task = conn.execute(
        "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()

    if not task:
        conn.close()
        return

    agent_id = task['agent_id']
    task_type = task['task_type']
    config = json.loads(task['config'] or '{}')

    # Mark running
    conn.execute(
        "UPDATE agent_tasks SET last_run_at = ?, last_status = 'running', last_error = NULL, updated_at = ? WHERE task_id = ?",
        (datetime.now().isoformat(), datetime.now().isoformat(), task_id)
    )
    conn.commit()
    conn.close()

    try:
        runner = get_runner(task_type)
        result = await runner.run(agent_id=agent_id, config=config)

        # Some tasks return None when there's nothing to report
        if result is None:
            conn = get_db()
            conn.execute(
                "UPDATE agent_tasks SET last_status = 'success', last_error = NULL, updated_at = ? WHERE task_id = ?",
                (datetime.now().isoformat(), task_id)
            )
            conn.commit()
            conn.close()
            print(f"[Scheduler] Task {task_id} ({task_type}) completed: nothing to report")
            return

        # Save insight
        insight_id = str(uuid.uuid4())[:16]
        now = datetime.now().isoformat()

        conn = get_db()
        conn.execute("""
            INSERT INTO agent_insights (insight_id, agent_id, task_id, task_type, title, content, summary, metadata, status, push_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unread', 'pending', ?)
        """, (
            insight_id, agent_id, task_id, task_type,
            result['title'], result['content'], result.get('summary', ''),
            json.dumps(result.get('metadata', {}), ensure_ascii=False),
            now
        ))

        # Update task status
        conn.execute(
            "UPDATE agent_tasks SET last_status = 'success', last_error = NULL, updated_at = ? WHERE task_id = ?",
            (now, task_id)
        )
        conn.commit()

        # Try IM push
        push_status = await try_push_im(agent_id, result)
        conn.execute(
            "UPDATE agent_insights SET push_status = ? WHERE insight_id = ?",
            (push_status, insight_id)
        )
        conn.commit()
        conn.close()

        print(f"[Scheduler] Task {task_id} ({task_type}) completed: {result.get('summary', '')}")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[Scheduler] Task {task_id} failed: {e}\n{tb}")
        conn = get_db()
        conn.execute(
            "UPDATE agent_tasks SET last_status = 'failed', last_error = ?, updated_at = ? WHERE task_id = ?",
            (str(e)[:500], datetime.now().isoformat(), task_id)
        )
        conn.commit()
        conn.close()


async def try_push_im(agent_id: str, result: dict) -> str:
    """Check agent im_config and push if available"""
    conn = get_db()
    row = conn.execute("SELECT im_config FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    conn.close()

    if not row:
        return 'no_im'

    im_config = json.loads(row['im_config'] or '{}')
    if not im_config:
        return 'no_im'

    # Support nested format: {"telegram": {"bot_token": ..., "chat_id": ...}}
    tg = im_config.get('telegram', {})
    # Also support flat format: {"provider": "telegram", "bot_token": ..., "chat_id": ...}
    if not tg:
        tg = im_config if im_config.get('provider') == 'telegram' else {}

    try:
        if tg:
            import httpx
            bot_token = tg.get('bot_token', '')
            chat_id = tg.get('chat_id', '')
            if bot_token and chat_id:
                text = f"**{result['title']}**\n\n{result.get('summary', '')}\n\n{result['content'][:3000]}"
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
                    )
                return 'pushed'
        # TODO: other IM providers
        return 'no_im'
    except Exception as e:
        print(f"[IM Push] Failed for {agent_id}: {e}")
        return 'push_failed'


def register_task(task_id: str, schedule: str):
    """Register a single task with the scheduler"""
    try:
        trigger_kwargs = parse_cron(schedule)
        trigger = CronTrigger(**trigger_kwargs)
        scheduler.add_job(
            execute_task, trigger,
            args=[task_id],
            id=task_id,
            replace_existing=True
        )
        print(f"[Scheduler] Registered task {task_id}: {schedule}")
    except Exception as e:
        print(f"[Scheduler] Failed to register {task_id}: {e}")


def unregister_task(task_id: str):
    """Remove a task from the scheduler"""
    try:
        scheduler.remove_job(task_id)
        print(f"[Scheduler] Unregistered task {task_id}")
    except Exception:
        pass


def load_all_tasks():
    """Load all enabled tasks from DB into scheduler"""
    conn = get_db()
    tasks = conn.execute("SELECT task_id, schedule FROM agent_tasks WHERE enabled = 1").fetchall()
    conn.close()

    for task in tasks:
        register_task(task['task_id'], task['schedule'])

    print(f"[Scheduler] Loaded {len(tasks)} tasks")


def start_scheduler():
    """Start the APScheduler"""
    load_all_tasks()
    if not scheduler.running:
        scheduler.start()
        print("[Scheduler] Started")


def shutdown_scheduler():
    """Shutdown the scheduler"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[Scheduler] Shutdown")
