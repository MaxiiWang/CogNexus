"""
Chat Session API — 对话会话管理

前缀: /api/agents/{agent_id}/chat
认证: JWT (get_current_user)
"""
import uuid
import json
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, Header, HTTPException
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import verify_token as verify_jwt
from database import get_db

router = APIRouter(prefix="/api/agents/{agent_id}/chat", tags=["chat"])


async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ")[1]
    payload = verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    return payload


async def verify_agent_access(agent_id: str, user: dict):
    """验证用户对 agent 的访问权限"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT owner_id, namespace, is_public FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    conn.close()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    # 所有者或公开 agent 可访问
    if agent["owner_id"] != user["user_id"] and agent["is_public"] != 1:
        raise HTTPException(status_code=403, detail="无权访问")
    return dict(agent)


# ==================== 请求模型 ====================

class SessionCreate(BaseModel):
    title: Optional[str] = None

class SessionUpdate(BaseModel):
    title: str


# ==================== 会话 CRUD ====================

@router.post("/sessions")
async def create_session(
    agent_id: str,
    data: SessionCreate = SessionCreate(),
    user: dict = Depends(get_current_user),
):
    """新建对话会话"""
    await verify_agent_access(agent_id, user)
    
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    title = data.title or "新对话"
    now = datetime.now().isoformat()
    
    conn = get_db()
    conn.execute("""
        INSERT INTO chat_sessions (session_id, agent_id, user_id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, agent_id, user["user_id"], title, now, now))
    conn.commit()
    conn.close()
    
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "title": title,
        "created_at": now,
        "message_count": 0,
    }


@router.get("/sessions")
async def list_sessions(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """获取会话列表"""
    await verify_agent_access(agent_id, user)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT session_id, agent_id, title, created_at, updated_at, message_count
        FROM chat_sessions
        WHERE agent_id = ? AND user_id = ?
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
    """, (agent_id, user["user_id"], limit, offset))
    
    sessions = [dict(row) for row in cursor.fetchall()]
    
    # 获取每个 session 的最后一条消息预览
    for s in sessions:
        cursor.execute("""
            SELECT content, role FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (s["session_id"],))
        last = cursor.fetchone()
        s["last_message"] = dict(last)["content"][:60] + "..." if last and len(dict(last)["content"]) > 60 else (dict(last)["content"] if last else None)
        s["last_role"] = dict(last)["role"] if last else None
    
    cursor.execute("""
        SELECT COUNT(*) as total FROM chat_sessions
        WHERE agent_id = ? AND user_id = ?
    """, (agent_id, user["user_id"]))
    total = cursor.fetchone()["total"]
    
    conn.close()
    return {"sessions": sessions, "total": total}


@router.delete("/sessions/{session_id}")
async def delete_session(
    agent_id: str,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """删除会话及其所有消息"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权
    cursor.execute("""
        SELECT session_id FROM chat_sessions
        WHERE session_id = ? AND agent_id = ? AND user_id = ?
    """, (session_id, agent_id, user["user_id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="会话不存在")
    
    cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "会话已删除"}


@router.put("/sessions/{session_id}")
async def update_session(
    agent_id: str,
    session_id: str,
    data: SessionUpdate,
    user: dict = Depends(get_current_user),
):
    """重命名会话"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT session_id FROM chat_sessions
        WHERE session_id = ? AND agent_id = ? AND user_id = ?
    """, (session_id, agent_id, user["user_id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="会话不存在")
    
    conn.execute("""
        UPDATE chat_sessions SET title = ?, updated_at = ? WHERE session_id = ?
    """, (data.title, datetime.now().isoformat(), session_id))
    conn.commit()
    conn.close()
    
    return {"success": True}


# ==================== 消息 ====================

@router.get("/sessions/{session_id}/messages")
async def get_messages(
    agent_id: str,
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """获取会话的历史消息"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权
    cursor.execute("""
        SELECT session_id FROM chat_sessions
        WHERE session_id = ? AND agent_id = ? AND user_id = ?
    """, (session_id, agent_id, user["user_id"]))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="会话不存在")
    
    cursor.execute("""
        SELECT message_id, role, content, sources_count, created_at
        FROM chat_messages
        WHERE session_id = ?
        ORDER BY created_at ASC
        LIMIT ? OFFSET ?
    """, (session_id, limit, offset))
    
    messages = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"messages": messages, "session_id": session_id}
