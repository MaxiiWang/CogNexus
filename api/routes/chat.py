"""
Chat Session API — 对话会话管理

前缀: /api/agents/{agent_id}/chat
认证: JWT (get_current_user)
"""
import uuid
import json
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, Header, HTTPException, UploadFile, File
from pydantic import BaseModel
import shutil
import zipfile
import tempfile

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


# ==================== Avatar 模型管理 ====================

avatar_router = APIRouter(prefix="/api", tags=["avatar"])


@avatar_router.get("/avatars/presets")
async def list_preset_avatars():
    """获取预置 Live2D 模型列表"""
    presets_dir = Path(__file__).parent.parent.parent / "data" / "avatars" / "presets"
    presets = []

    # 预定义的模型信息
    preset_info = {
        "haru": {"name": "春 (Haru)", "description": "活泼开朗的少女", "style": "日系"},
        "hiyori": {"name": "日和 (Hiyori)", "description": "温柔甜美的女孩", "style": "甜美"},
        "mao": {"name": "猫 (Mao)", "description": "猫耳萌系角色", "style": "萌系"},
        "mark": {"name": "Mark", "description": "简约男性角色", "style": "简约"},
        "natori": {"name": "名取 (Natori)", "description": "成熟知性的女性", "style": "知性"},
        "rice": {"name": "Rice", "description": "Q版可爱角色", "style": "Q版"},
    }

    if presets_dir.exists():
        for d in sorted(presets_dir.iterdir()):
            if d.is_dir():
                model_files = list(d.glob("*.model3.json"))
                if model_files:
                    slug = d.name
                    info = preset_info.get(slug, {"name": slug, "description": "", "style": ""})
                    entry_file = model_files[0].name
                    presets.append({
                        "id": slug,
                        "name": info["name"],
                        "description": info["description"],
                        "style": info["style"],
                        "model_url": f"/avatars/presets/{slug}/{entry_file}",
                        "preview_url": f"/avatars/presets/{slug}/{entry_file}",
                    })

    return {"presets": presets}


class AvatarSetRequest(BaseModel):
    preset: Optional[str] = None
    avatar_model_url: Optional[str] = None
    clear: bool = False


@avatar_router.put("/agents/{agent_id}/avatar")
async def set_agent_avatar(
    agent_id: str,
    data: AvatarSetRequest,
    user: dict = Depends(get_current_user),
):
    """设置 Agent 的 Avatar（预置/自定义/清除）"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权修改")

    if data.clear:
        conn.execute("UPDATE agents SET avatar_model_url = NULL WHERE agent_id = ?", (agent_id,))
        conn.commit()
        conn.close()
        return {"success": True, "avatar_model_url": None}

    if data.preset:
        presets_dir = Path(__file__).parent.parent.parent / "data" / "avatars" / "presets" / data.preset
        if not presets_dir.exists():
            conn.close()
            raise HTTPException(status_code=404, detail=f"预置模型 '{data.preset}' 不存在")
        model_files = list(presets_dir.glob("*.model3.json"))
        if not model_files:
            conn.close()
            raise HTTPException(status_code=404, detail="模型文件不完整")
        url = f"/avatars/presets/{data.preset}/{model_files[0].name}"
        conn.execute("UPDATE agents SET avatar_model_url = ? WHERE agent_id = ?", (url, agent_id))
        conn.commit()
        conn.close()
        return {"success": True, "avatar_model_url": url}

    if data.avatar_model_url:
        conn.execute("UPDATE agents SET avatar_model_url = ? WHERE agent_id = ?", (data.avatar_model_url, agent_id))
        conn.commit()
        conn.close()
        return {"success": True, "avatar_model_url": data.avatar_model_url}

    conn.close()
    raise HTTPException(status_code=400, detail="请指定 preset、avatar_model_url 或 clear")


@avatar_router.post("/agents/{agent_id}/avatar/upload")
async def upload_avatar(
    agent_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """上传自定义 Live2D 模型 (.zip，限制 20MB)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权修改")
    conn.close()

    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="请上传 .zip 格式的模型文件")

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 20MB")

    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "model.zip"
        zip_path.write_bytes(content)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    if '..' in name or name.startswith('/'):
                        raise HTTPException(status_code=400, detail=f"不安全的文件路径: {name}")

                allowed_exts = {'.json', '.moc3', '.png', '.jpg', '.jpeg', '.motion3.json',
                               '.exp3.json', '.physics3.json', '.pose3.json', '.cdi3.json',
                               '.userdata3.json'}

                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="无效的 zip 文件")

        extract_dir = Path(tmp_dir)
        model_files = list(extract_dir.rglob("*.model3.json"))
        if not model_files:
            raise HTTPException(status_code=400, detail="zip 中未找到 .model3.json 文件")

        model_root = model_files[0].parent

        moc3_files = list(model_root.rglob("*.moc3"))
        if not moc3_files:
            raise HTTPException(status_code=400, detail="zip 中未找到 .moc3 文件")

        dest_dir = Path(__file__).parent.parent.parent / "data" / "avatars" / agent_id
        if dest_dir.exists():
            shutil.rmtree(dest_dir)

        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in model_root.rglob("*"):
            if f.is_file():
                suffix = ''.join(f.suffixes).lower()
                is_allowed = any(suffix.endswith(ext) for ext in allowed_exts)
                if is_allowed:
                    rel = f.relative_to(model_root)
                    dest = dest_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

        entry_file = model_files[0].name
        model_url = f"/avatars/{agent_id}/{entry_file}"

        conn = get_db()
        conn.execute("UPDATE agents SET avatar_model_url = ? WHERE agent_id = ?", (model_url, agent_id))
        conn.commit()
        conn.close()

        return {
            "success": True,
            "avatar_model_url": model_url,
            "files_count": len(list(dest_dir.rglob("*"))),
            "message": "模型上传成功"
        }
