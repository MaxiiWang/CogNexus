"""
Agent Tasks & Insights API Routes
"""
import json
import uuid
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

from database import get_db
from auth import verify_token

router = APIRouter(prefix="/api/agents", tags=["tasks"])


# ==================== Auth Helper ====================

async def get_current_user(authorization: str = None):
    """Simplified auth - extract from header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def _check_agent_owner(agent_id: str, user_id: str):
    conn = get_db()
    agent = conn.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    conn.close()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent['owner_id'] != user_id:
        raise HTTPException(status_code=403, detail="Not owner")
    return True


# ==================== Task Types ====================

@router.get("/meta/task-types")
async def get_task_types():
    """Get all available task types with metadata"""
    from task_runners import TASK_TYPES
    return {"task_types": TASK_TYPES}


# ==================== Task CRUD ====================

class TaskCreate(BaseModel):
    task_type: str
    schedule: str  # cron expression
    enabled: bool = True
    config: dict = {}


class TaskUpdate(BaseModel):
    schedule: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict] = None


@router.get("/{agent_id}/tasks")
async def list_tasks(agent_id: str, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    conn = get_db()
    tasks = conn.execute(
        "SELECT * FROM agent_tasks WHERE agent_id = ? ORDER BY created_at", (agent_id,)
    ).fetchall()
    conn.close()

    return {"tasks": [dict(t) for t in tasks]}


@router.post("/{agent_id}/tasks")
async def create_task(agent_id: str, data: TaskCreate, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    from task_runners import TASK_TYPES
    if data.task_type not in TASK_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {data.task_type}")

    # Validate cron
    from scheduler import parse_cron, register_task
    try:
        parse_cron(data.schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check duplicate
    conn = get_db()
    existing = conn.execute(
        "SELECT task_id FROM agent_tasks WHERE agent_id = ? AND task_type = ?",
        (agent_id, data.task_type)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail=f"Task type '{data.task_type}' already exists for this agent")

    task_id = f"task_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO agent_tasks (task_id, agent_id, task_type, enabled, schedule, config, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (task_id, agent_id, data.task_type, int(data.enabled), data.schedule,
          json.dumps(data.config, ensure_ascii=False), now, now))
    conn.commit()
    conn.close()

    if data.enabled:
        register_task(task_id, data.schedule)

    return {"task_id": task_id, "status": "created"}


@router.put("/{agent_id}/tasks/{task_id}")
async def update_task(agent_id: str, task_id: str, data: TaskUpdate, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    from scheduler import register_task, unregister_task, parse_cron

    conn = get_db()
    task = conn.execute(
        "SELECT * FROM agent_tasks WHERE task_id = ? AND agent_id = ?", (task_id, agent_id)
    ).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")

    updates = []
    params = []

    if data.schedule is not None:
        try:
            parse_cron(data.schedule)
        except ValueError as e:
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
        updates.append("schedule = ?")
        params.append(data.schedule)

    if data.enabled is not None:
        updates.append("enabled = ?")
        params.append(int(data.enabled))

    if data.config is not None:
        updates.append("config = ?")
        params.append(json.dumps(data.config, ensure_ascii=False))

    if updates:
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(task_id)
        conn.execute(f"UPDATE agent_tasks SET {', '.join(updates)} WHERE task_id = ?", params)
        conn.commit()

    conn.close()

    # Re-register or unregister
    new_enabled = data.enabled if data.enabled is not None else bool(task['enabled'])
    new_schedule = data.schedule or task['schedule']

    if new_enabled:
        register_task(task_id, new_schedule)
    else:
        unregister_task(task_id)

    return {"status": "updated"}


@router.delete("/{agent_id}/tasks/{task_id}")
async def delete_task(agent_id: str, task_id: str, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    from scheduler import unregister_task

    conn = get_db()
    conn.execute("DELETE FROM agent_tasks WHERE task_id = ? AND agent_id = ?", (task_id, agent_id))
    conn.commit()
    conn.close()

    unregister_task(task_id)
    return {"status": "deleted"}


@router.post("/{agent_id}/tasks/{task_id}/run")
async def run_task_now(agent_id: str, task_id: str, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    conn = get_db()
    task = conn.execute(
        "SELECT * FROM agent_tasks WHERE task_id = ? AND agent_id = ?", (task_id, agent_id)
    ).fetchone()
    conn.close()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Run in background
    from scheduler import execute_task
    asyncio.create_task(execute_task(task_id))

    return {"status": "triggered", "task_id": task_id}


class TaskTestRun(BaseModel):
    task_type: str
    config: dict = {}


@router.post("/{agent_id}/tasks/test-run")
async def test_run_task(agent_id: str, data: TaskTestRun, authorization: str = None):
    """Run a task type once without creating a persistent task. Returns result directly."""
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    from task_runners import TASK_TYPES, get_runner
    if data.task_type not in TASK_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid task_type: {data.task_type}")

    try:
        runner = get_runner(data.task_type)
    except ValueError as e:
        raise HTTPException(status_code=501, detail=str(e))

    default_cfg = TASK_TYPES[data.task_type].get('default_config', {})
    config = {**default_cfg, **data.config}

    try:
        result = await runner.run(agent_id=agent_id, config=config)

        # Save as insight
        insight_id = str(uuid.uuid4())[:16]
        now = datetime.now().isoformat()
        conn = get_db()
        conn.execute("""
            INSERT INTO agent_insights (insight_id, agent_id, task_id, task_type, title, content, summary, metadata, status, push_status, created_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'unread', 'no_im', ?)
        """, (
            insight_id, agent_id, data.task_type,
            result['title'], result['content'], result.get('summary', ''),
            json.dumps(result.get('metadata', {}), ensure_ascii=False), now
        ))
        conn.commit()
        conn.close()

        return {
            "status": "success",
            "insight_id": insight_id,
            "title": result['title'],
            "summary": result.get('summary', ''),
            "content": result['content'],
            "metadata": result.get('metadata', {}),
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Insights ====================

@router.get("/{agent_id}/insights")
async def list_insights(
    agent_id: str,
    task_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(20, le=100),
    offset: int = 0,
    authorization: str = None
):
    """List insights for an agent. Owner sees all, visitors see only if agent is public."""
    conn = get_db()
    agent = conn.execute("SELECT owner_id, is_public FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent not found")

    # Auth: owner or public agent
    is_owner = False
    if authorization:
        try:
            user = await get_current_user(authorization)
            is_owner = (user['user_id'] == agent['owner_id'])
        except Exception:
            pass

    if not is_owner and not agent['is_public']:
        conn.close()
        raise HTTPException(status_code=403, detail="Not authorized")

    query = "SELECT * FROM agent_insights WHERE agent_id = ?"
    params = [agent_id]

    if task_type:
        query += " AND task_type = ?"
        params.append(task_type)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()

    # Total count
    count_query = "SELECT COUNT(*) as c FROM agent_insights WHERE agent_id = ?"
    count_params = [agent_id]
    if task_type:
        count_query += " AND task_type = ?"
        count_params.append(task_type)
    if status:
        count_query += " AND status = ?"
        count_params.append(status)
    total = conn.execute(count_query, count_params).fetchone()['c']
    conn.close()

    return {
        "insights": [dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{agent_id}/insights/unread-count")
async def unread_count(agent_id: str, authorization: str = None):
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM agent_insights WHERE agent_id = ? AND status = 'unread'",
        (agent_id,)
    ).fetchone()
    conn.close()
    return {"count": row['c'] if row else 0}


@router.put("/{agent_id}/insights/{insight_id}/read")
async def mark_read(agent_id: str, insight_id: str, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    conn = get_db()
    conn.execute(
        "UPDATE agent_insights SET status = 'read' WHERE insight_id = ? AND agent_id = ?",
        (insight_id, agent_id)
    )
    conn.commit()
    conn.close()
    return {"status": "read"}


@router.put("/{agent_id}/insights/{insight_id}/archive")
async def archive_insight(agent_id: str, insight_id: str, authorization: str = None):
    user = await get_current_user(authorization)
    _check_agent_owner(agent_id, user['user_id'])

    conn = get_db()
    conn.execute(
        "UPDATE agent_insights SET status = 'archived' WHERE insight_id = ? AND agent_id = ?",
        (insight_id, agent_id)
    )
    conn.commit()
    conn.close()
    return {"status": "archived"}
