"""
Settings API — 用户全局配置

端点:
  GET  /api/settings       获取当前用户配置
  PUT  /api/settings/llm   更新 LLM 配置
  PUT  /api/settings/im    更新 IM 配置
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import verify_token
from database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ==================== 依赖 ====================

async def get_current_user(authorization: str = Header(None)):
    """获取当前用户（JWT 认证）"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")

    token = authorization.split(" ")[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    return payload


# ==================== 请求模型 ====================

class LLMSettings(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class IMSettings(BaseModel):
    platform: Optional[str] = None       # telegram / slack / wechat
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    webhook_url: Optional[str] = None


# ==================== 端点 ====================

@router.get("")
async def get_settings(user: dict = Depends(get_current_user)):
    """获取当前用户的全局配置"""
    user_id = user["user_id"]
    conn = get_db()
    cursor = conn.cursor()

    # 用户级别配置
    cursor.execute(
        "SELECT * FROM user_settings WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()

    user_settings = {}
    if row:
        user_settings = {
            "llm": {
                "provider": row["default_llm_provider"],
                "model": row["default_model"],
                "has_key": bool(row["default_llm_key_encrypted"]),
            },
            "ui_language": row["ui_language"],
        }
    else:
        user_settings = {
            "llm": {"provider": None, "model": None, "has_key": False},
            "ui_language": "en",
        }

    # 该用户所有 agent 的 IM 配置概览
    cursor.execute(
        "SELECT agent_id, name, im_config FROM agents WHERE owner_id = ?",
        (user_id,)
    )
    agents_im = []
    for agent in cursor.fetchall():
        im_cfg = json.loads(agent["im_config"] or "{}")
        agents_im.append({
            "agent_id": agent["agent_id"],
            "name": agent["name"],
            "im_connected": bool(im_cfg),
            "im_platform": im_cfg.get("platform"),
        })

    conn.close()

    return {
        "user": user_settings,
        "agents_im": agents_im,
    }


@router.put("/llm")
async def update_llm_settings(
    data: LLMSettings,
    user: dict = Depends(get_current_user),
):
    """更新 LLM 配置"""
    user_id = user["user_id"]
    conn = get_db()
    cursor = conn.cursor()

    # upsert user_settings
    cursor.execute(
        "SELECT user_id FROM user_settings WHERE user_id = ?",
        (user_id,)
    )
    exists = cursor.fetchone()
    now = datetime.utcnow().isoformat()

    if exists:
        cursor.execute("""
            UPDATE user_settings
            SET default_llm_provider = COALESCE(?, default_llm_provider),
                default_llm_key_encrypted = COALESCE(?, default_llm_key_encrypted),
                default_model = COALESCE(?, default_model),
                updated_at = ?
            WHERE user_id = ?
        """, (data.provider, data.api_key, data.model, now, user_id))
    else:
        cursor.execute("""
            INSERT INTO user_settings
                (user_id, default_llm_provider, default_llm_key_encrypted, default_model, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, data.provider, data.api_key, data.model, now))

    conn.commit()
    conn.close()

    return {"success": True, "message": "LLM 配置已更新"}


@router.put("/im")
async def update_im_settings(
    data: IMSettings,
    user: dict = Depends(get_current_user),
):
    """更新指定 Agent 的 IM 配置（通过 query param agent_id）"""
    from fastapi import Query as _Query

    # IM 配置绑定到 agent 而非全局用户
    # 前端通过 body 里的 agent_id 或者单独的 query param 指定
    user_id = user["user_id"]
    conn = get_db()
    cursor = conn.cursor()

    # 获取用户的第一个 agent（简化版，后续可通过 agent_id 参数指定）
    cursor.execute(
        "SELECT agent_id FROM agents WHERE owner_id = ? LIMIT 1",
        (user_id,)
    )
    agent_row = cursor.fetchone()

    if not agent_row:
        conn.close()
        raise HTTPException(status_code=404, detail="未找到 Agent，请先创建")

    agent_id = agent_row["agent_id"]

    im_config = {}
    if data.platform:
        im_config["platform"] = data.platform
    if data.bot_token:
        im_config["bot_token"] = data.bot_token
    if data.chat_id:
        im_config["chat_id"] = data.chat_id
    if data.webhook_url:
        im_config["webhook_url"] = data.webhook_url

    cursor.execute(
        "UPDATE agents SET im_config = ?, updated_at = ? WHERE agent_id = ? AND owner_id = ?",
        (json.dumps(im_config), datetime.utcnow().isoformat(), agent_id, user_id)
    )

    conn.commit()
    conn.close()

    return {"success": True, "agent_id": agent_id, "message": "IM 配置已更新"}
