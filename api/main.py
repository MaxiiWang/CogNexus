"""
CogNexus API - 分布式认知枢纽
"""
import uuid
import json
import httpx
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from pathlib import Path

from database import get_db, init_db
from auth import (
    hash_password, verify_password, 
    create_token, verify_token, 
    generate_agent_token
)

# 初始化数据库
init_db()

app = FastAPI(
    title="CogNexus - 分布式认知枢纽",
    description="连接 Human、Character、Simulate",
    version="0.1.0",
    docs_url=None,      # 禁用 Swagger UI
    redoc_url=None,     # 禁用 ReDoc
    openapi_url=None    # 禁用 OpenAPI schema
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
FRONTEND_PATH = Path(__file__).parent.parent / "frontend"


# ==================== 数据模型 ====================

class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    agent_type: str = "human"
    endpoint_url: str
    namespace: str = "default"  # Cogmate namespace
    avatar_url: Optional[str] = None
    tags: Optional[List[str]] = []
    price_chat: int = 10
    price_read: int = 5
    price_react: int = 20
    tokens: Optional[List[str]] = []  # 用户提供的 Token 列表

class TokenPurchase(BaseModel):
    agent_id: str

class AgentProbe(BaseModel):
    url: str

class AgentImport(BaseModel):
    """从 Cogmate 导入 Agent"""
    source_url: str  # Cogmate API 地址
    namespace: str   # 角色 namespace
    profile: dict    # 角色信息 {name, title, type, avatar, bio, fact_count}
    tokens: List[dict] = []  # Token 列表 [{value, scope, qa_limit, unit_price}]
    price_react: Optional[int] = None  # React 定价 (ATP)


# ==================== 依赖 ====================

async def get_current_user(authorization: str = Header(None)):
    """获取当前用户"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    
    token = authorization.split(" ")[1]
    payload = verify_token(token)
    
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    
    return payload


# ==================== 认证 API ====================

@app.post("/api/auth/register")
async def register(data: UserRegister):
    """用户注册"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 检查用户名和邮箱是否已存在
    cursor.execute(
        "SELECT user_id FROM users WHERE username = ? OR email = ?",
        (data.username, data.email)
    )
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="用户名或邮箱已存在")
    
    # 创建用户
    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    password_hash = hash_password(data.password)
    
    cursor.execute("""
        INSERT INTO users (user_id, username, email, password_hash, atp_balance)
        VALUES (?, ?, ?, ?, 100)
    """, (user_id, data.username, data.email, password_hash))
    
    # 记录注册奖励交易
    tx_id = f"tx_{uuid.uuid4().hex[:12]}"
    cursor.execute("""
        INSERT INTO transactions (tx_id, to_user_id, atp_amount, tx_type, description)
        VALUES (?, ?, 100, 'register', '注册奖励')
    """, (tx_id, user_id))
    
    conn.commit()
    conn.close()
    
    # 生成 Token
    token = create_token(user_id, data.username)
    
    return {
        "user_id": user_id,
        "username": data.username,
        "atp_balance": 100,
        "token": token,
        "message": "注册成功，已获得 100 ATP"
    }


@app.post("/api/auth/login")
async def login(data: UserLogin):
    """用户登录"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT user_id, username, password_hash, atp_balance FROM users WHERE username = ?",
        (data.username,)
    )
    user = cursor.fetchone()
    conn.close()
    
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    
    token = create_token(user["user_id"], user["username"])
    
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "atp_balance": user["atp_balance"],
        "token": token
    }


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
    """获取当前用户信息"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT user_id, username, email, atp_balance, created_at FROM users WHERE user_id = ?",
        (user["user_id"],)
    )
    user_data = cursor.fetchone()
    conn.close()
    
    if not user_data:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    return dict(user_data)


# ==================== Agent API ====================

# ==================== Agent 探测 API ====================

@app.post("/api/agents/probe")
async def probe_agent(data: AgentProbe):
    """探测 Agent URL，获取所有角色列表"""
    import httpx
    
    url = data.url.rstrip('/')
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 尝试获取所有角色列表
            profiles_url = f"{url}/api/hub/profiles"
            try:
                res = await client.get(profiles_url)
                if res.status_code == 200:
                    data = res.json()
                    profiles = data.get("profiles", [])
                    if profiles:
                        return {
                            "success": True,
                            "profiles": profiles,
                            "api_version": "2.0"
                        }
            except:
                pass
            
            # 降级：尝试获取单个 Hub profile
            profile_url = f"{url}/api/hub/profile"
            try:
                res = await client.get(profile_url)
                if res.status_code == 200:
                    profile = res.json()
                    return {
                        "success": True,
                        "profiles": [{
                            "namespace": "default",
                            "type": "human",
                            "name": profile.get("name", ""),
                            "title": profile.get("title", ""),
                            "avatar": profile.get("avatar", ""),
                            "fact_count": profile.get("stats", {}).get("facts", 0)
                        }],
                        "api_version": "1.0"
                    }
            except:
                pass
            
            # 降级：尝试获取公开 profile
            public_profile_url = f"{url}/api/public/profile"
            try:
                res = await client.get(public_profile_url)
                if res.status_code == 200:
                    profile = res.json()
                    return {
                        "success": True,
                        "profiles": [{
                            "namespace": "default",
                            "type": "human",
                            "name": profile.get("name", ""),
                            "title": profile.get("title", ""),
                            "avatar": profile.get("avatar", ""),
                            "fact_count": 0
                        }],
                        "api_version": "legacy"
                    }
            except:
                pass
            
            # 尝试健康检查
            health_url = f"{url}/health"
            try:
                res = await client.get(health_url)
                if res.status_code == 200:
                    return {
                        "success": True,
                        "profiles": [],
                        "api_version": "minimal",
                        "message": "Agent 在线，但未配置 profile"
                    }
            except:
                pass
            
            return {"success": False, "error": "无法连接到 Agent"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/agents/health-check")
async def check_agent_health(agent_id: str = None):
    """检测 Agent 健康状态"""
    import httpx
    
    conn = get_db()
    cursor = conn.cursor()
    
    if agent_id:
        cursor.execute("SELECT agent_id, endpoint_url FROM agents WHERE agent_id = ?", (agent_id,))
    else:
        cursor.execute("SELECT agent_id, endpoint_url FROM agents WHERE status = 'active'")
    
    agents = cursor.fetchall()
    results = []
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        for agent in agents:
            aid, url = agent["agent_id"], agent["endpoint_url"]
            url = url.rstrip('/')
            
            try:
                res = await client.get(f"{url}/health")
                online = res.status_code == 200
            except:
                online = False
            
            # 更新状态
            new_status = "active" if online else "offline"
            cursor.execute(
                "UPDATE agents SET status = ?, updated_at = datetime('now') WHERE agent_id = ?",
                (new_status, aid)
            )
            
            results.append({
                "agent_id": aid,
                "online": online,
                "status": new_status
            })
    
    conn.commit()
    conn.close()
    
    return {"checked": len(results), "results": results}


@app.get("/api/agents")
async def list_agents():
    """列出所有公开 Agent"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取 Agent 基本信息
    cursor.execute("""
        SELECT a.*, u.username as owner_name
        FROM agents a
        JOIN users u ON a.owner_id = u.user_id
        ORDER BY a.created_at DESC
    """)
    
    agents = []
    for row in cursor.fetchall():
        agent = dict(row)
        agent_id = agent["agent_id"]
        
        # 获取各类型 Token 统计
        cursor.execute("""
            SELECT scope, scope_label, qa_limit, unit_price, expires_at, COUNT(*) as total,
                   SUM(CASE WHEN is_sold = 0 THEN 1 ELSE 0 END) as available
            FROM agent_tokens 
            WHERE agent_id = ? AND validated = 1
            GROUP BY scope, qa_limit
        """, (agent_id,))
        
        token_types = []
        for t in cursor.fetchall():
            token_types.append({
                "scope": t["scope"],
                "scope_label": t["scope_label"] or t["scope"],
                "qa_limit": t["qa_limit"],
                "unit_price": t["unit_price"] or 0,
                "expires_at": t["expires_at"],
                "total": t["total"],
                "available": t["available"]
            })
        
        agent["token_types"] = token_types
        agent["total_available"] = sum(t["available"] for t in token_types)
        agents.append(agent)
    
    conn.close()
    
    return {"agents": agents, "total": len(agents)}


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str):
    """获取 Agent 详情"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT a.*, u.username as owner_name,
               (SELECT price_chat FROM agent_tokens WHERE agent_id = a.agent_id LIMIT 1) as price_chat,
               (SELECT price_read FROM agent_tokens WHERE agent_id = a.agent_id LIMIT 1) as price_read,
               (SELECT price_react FROM agent_tokens WHERE agent_id = a.agent_id LIMIT 1) as price_react,
               (SELECT COUNT(*) FROM agent_tokens WHERE agent_id = a.agent_id AND is_sold = 0) as available_tokens,
               (SELECT COUNT(*) FROM agent_tokens WHERE agent_id = a.agent_id AND is_sold = 1) as sold_tokens
        FROM agents a
        JOIN users u ON a.owner_id = u.user_id
        WHERE a.agent_id = ?
    """, (agent_id,))
    
    agent = cursor.fetchone()
    conn.close()
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    return dict(agent)


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, data: AgentCreate, user: dict = Depends(get_current_user)):
    """更新 Agent"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权
    cursor.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权修改此 Agent")
    
    tags_str = json.dumps(data.tags) if data.tags else "[]"
    
    cursor.execute("""
        UPDATE agents SET name = ?, description = ?, agent_type = ?, 
                         endpoint_url = ?, avatar_url = ?, tags = ?,
                         updated_at = datetime('now')
        WHERE agent_id = ?
    """, (data.name, data.description, data.agent_type, 
          data.endpoint_url, data.avatar_url, tags_str, agent_id))
    
    # 更新定价
    cursor.execute("""
        UPDATE agent_tokens SET price_chat = ?, price_read = ?, price_react = ?
        WHERE agent_id = ?
    """, (data.price_chat, data.price_read, data.price_react, agent_id))
    
    # 如果提供了新 Token，添加到列表
    if data.tokens and len(data.tokens) > 0:
        for token_value in data.tokens:
            if token_value.strip():
                token_id = f"tkn_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO agent_tokens (token_id, agent_id, token_value, permissions,
                                             price_chat, price_read, price_react)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (token_id, agent_id, token_value.strip(), '["chat","read","react"]',
                      data.price_chat, data.price_read, data.price_react))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Agent 更新成功"}


@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: str, user: dict = Depends(get_current_user)):
    """删除 Agent"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权
    cursor.execute("SELECT owner_id, name FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权删除此 Agent")
    
    # 删除关联的 Token
    cursor.execute("DELETE FROM agent_tokens WHERE agent_id = ?", (agent_id,))
    
    # 删除 Agent
    cursor.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": f"Agent '{agent['name']}' 已删除"}


@app.get("/api/agents/{agent_id}/tokens")
async def get_agent_tokens(agent_id: str, user: dict = Depends(get_current_user)):
    """获取 Agent 的 Token 列表（仅所有者可见）"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权
    cursor.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权查看此 Agent 的 Token")
    
    cursor.execute("""
        SELECT token_id, token_value, scope, scope_label, qa_limit, unit_price, 
               expires_at, is_sold, sold_to_user_id, sold_at, validated, created_at
        FROM agent_tokens WHERE agent_id = ?
    """, (agent_id,))
    
    tokens = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    available = len([t for t in tokens if not t["is_sold"]])
    sold = len([t for t in tokens if t["is_sold"]])
    
    return {
        "agent_id": agent_id,
        "tokens": tokens,
        "total": len(tokens),
        "available": available,
        "sold": sold
    }


class AddTokensRequest(BaseModel):
    tokens: List[str]


async def validate_token_with_cogmate(endpoint_url: str, token_value: str) -> dict:
    """调用 Cogmate API 验证 Token 并获取元数据"""
    url = endpoint_url.rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{url}/api/hub/token/validate", params={"token": token_value})
            if res.status_code == 200:
                return res.json()
    except:
        pass
    return {"valid": False, "error": "connection_failed"}


@app.post("/api/agents/{agent_id}/tokens")
async def add_agent_tokens(agent_id: str, data: AddTokensRequest, user: dict = Depends(get_current_user)):
    """向 Agent 添加 Token（自动验证并获取元数据）"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权并获取 endpoint_url
    cursor.execute("SELECT owner_id, name, endpoint_url FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权修改此 Agent")
    
    endpoint_url = agent["endpoint_url"]
    
    # 添加并验证 Token
    added = 0
    failed = 0
    results = []
    
    for token_value in data.tokens:
        token_value = token_value.strip()
        if not token_value:
            continue
        
        # 验证 Token
        validation = await validate_token_with_cogmate(endpoint_url, token_value)
        
        token_id = f"tkn_{uuid.uuid4().hex[:12]}"
        
        if validation.get("valid"):
            usage = validation.get("usage", {})
            scope = validation.get("scope", "unknown")
            scope_label = validation.get("scope_label", scope)
            qa_limit = usage.get("qa_limit", 0)
            expires_at = validation.get("expires_at", "")
            permissions = json.dumps(validation.get("permissions", []))
            
            cursor.execute("""
                INSERT INTO agent_tokens (token_id, agent_id, token_value, permissions,
                                         scope, scope_label, qa_limit, expires_at, 
                                         is_sold, validated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
            """, (token_id, agent_id, token_value, permissions,
                  scope, scope_label, qa_limit, expires_at))
            added += 1
            results.append({"token": token_value[:8] + "...", "status": "valid", "scope": scope_label, "qa_limit": qa_limit})
        else:
            # 即使验证失败也添加，但标记为未验证
            cursor.execute("""
                INSERT INTO agent_tokens (token_id, agent_id, token_value, permissions,
                                         scope, is_sold, validated)
                VALUES (?, ?, ?, ?, 'unknown', 0, 0)
            """, (token_id, agent_id, token_value, '[]'))
            failed += 1
            results.append({"token": token_value[:8] + "...", "status": "invalid", "error": validation.get("error", "unknown")})
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "added": added,
        "failed": failed,
        "results": results,
        "message": f"已添加 {added} 个有效 Token" + (f"，{failed} 个验证失败" if failed else "")
    }


class TokenPricing(BaseModel):
    scope: str
    qa_limit: int
    unit_price: float


@app.put("/api/agents/{agent_id}/pricing")
async def set_token_pricing(agent_id: str, pricing: List[TokenPricing], user: dict = Depends(get_current_user)):
    """设置 Token 单价"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 验证所有权
    cursor.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    if agent["owner_id"] != user["user_id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="无权修改此 Agent")
    
    updated = 0
    for p in pricing:
        cursor.execute("""
            UPDATE agent_tokens SET unit_price = ?
            WHERE agent_id = ? AND scope = ? AND qa_limit = ?
        """, (p.unit_price, agent_id, p.scope, p.qa_limit))
        updated += cursor.rowcount
    
    conn.commit()
    conn.close()
    
    return {"success": True, "updated": updated}


@app.post("/api/agents/import")
async def import_agent(data: AgentImport, user: dict = Depends(get_current_user)):
    """从 Cogmate 导入 Agent（由 Cogmate 调用）"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 检查是否已存在（按 source_url + namespace 匹配）
    cursor.execute("""
        SELECT agent_id FROM agents 
        WHERE source_url = ? AND namespace = ?
    """, (data.source_url, data.namespace))
    existing = cursor.fetchone()
    
    now = datetime.now().isoformat()
    
    if existing:
        # 更新现有 Agent
        agent_id = existing["agent_id"]
        cursor.execute("""
            UPDATE agents SET 
                name = ?, description = ?, agent_type = ?, avatar_url = ?,
                last_synced_at = ?, updated_at = ?
            WHERE agent_id = ?
        """, (
            data.profile.get("name", ""),
            data.profile.get("bio", data.profile.get("title", "")),
            data.profile.get("type", "human"),
            data.profile.get("avatar", ""),
            now, now, agent_id
        ))
        created = False
    else:
        # 创建新 Agent
        agent_id = f"agt_{uuid.uuid4().hex[:12]}"
        cursor.execute("""
            INSERT INTO agents (
                agent_id, owner_id, name, description, agent_type,
                endpoint_url, namespace, avatar_url, tags,
                source, source_url, last_synced_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            agent_id, user["user_id"],
            data.profile.get("name", ""),
            data.profile.get("bio", data.profile.get("title", "")),
            data.profile.get("type", "human"),
            data.source_url,
            data.namespace,
            data.profile.get("avatar", ""),
            "[]",
            "cogmate",
            data.source_url,
            now,
            "active"
        ))
        created = True
    
    # Update price_react on existing tokens if provided
    if data.price_react is not None:
        cursor.execute(
            "UPDATE agent_tokens SET price_react = ? WHERE agent_id = ?",
            (data.price_react, agent_id)
        )

    # 导入 Tokens
    tokens_imported = 0
    for t in data.tokens:
        token_value = t.get("value", "").strip()
        if not token_value:
            continue

        # 检查 token 是否已存在
        cursor.execute("SELECT token_id FROM agent_tokens WHERE token_value = ?", (token_value,))
        if cursor.fetchone():
            continue  # 跳过已存在的 token

        token_id = f"tkn_{uuid.uuid4().hex[:12]}"
        cursor.execute("""
            INSERT INTO agent_tokens (
                token_id, agent_id, token_value, permissions,
                scope, qa_limit, unit_price, namespace, validated, price_react
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token_id, agent_id, token_value,
            '["chat","read"]',
            t.get("scope", "qa_public"),
            t.get("qa_limit", 20),
            t.get("unit_price", 5),
            data.namespace,
            1,  # 从 Cogmate 导入的默认已验证
            data.price_react or 0
        ))
        tokens_imported += 1

    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "agent_id": agent_id,
        "created": created,
        "tokens_imported": tokens_imported,
        "agent_url": f"/marketplace.html#agent={agent_id}"
    }


@app.get("/api/agents/{agent_id}/sync")
async def sync_agent(agent_id: str):
    """同步 Agent 信息（从 Cogmate 拉取最新）"""
    import httpx
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT source_url, namespace, name, agent_type 
        FROM agents WHERE agent_id = ?
    """, (agent_id,))
    agent = cursor.fetchone()
    
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    if not agent["source_url"]:
        conn.close()
        return {"synced": False, "reason": "非 Cogmate 导入的 Agent"}
    
    # 调用 Cogmate API 获取最新信息
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{agent['source_url'].rstrip('/')}/api/hub/profile?ns={agent['namespace']}"
            res = await client.get(url)
            if res.status_code != 200:
                conn.close()
                return {"synced": False, "reason": "无法连接 Cogmate"}
            
            profile = res.json()
    except Exception as e:
        conn.close()
        return {"synced": False, "reason": str(e)}
    
    # 比较变化
    changes = {}
    new_name = profile.get("name", "")
    new_bio = profile.get("bio", "")
    new_facts = profile.get("stats", {}).get("facts", 0)
    
    if new_name and new_name != agent["name"]:
        changes["name"] = {"old": agent["name"], "new": new_name}
    
    # 更新数据库
    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE agents SET 
            name = COALESCE(NULLIF(?, ''), name),
            description = COALESCE(NULLIF(?, ''), description),
            last_synced_at = ?
        WHERE agent_id = ?
    """, (new_name, new_bio, now, agent_id))
    
    conn.commit()
    conn.close()
    
    return {
        "synced": True,
        "changes": changes,
        "fact_count": new_facts,
        "synced_at": now
    }


@app.post("/api/agents")
async def create_agent(data: AgentCreate, user: dict = Depends(get_current_user)):
    """创建 Agent"""
    conn = get_db()
    cursor = conn.cursor()
    
    agent_id = f"agt_{uuid.uuid4().hex[:12]}"
    tags_str = json.dumps(data.tags) if data.tags else "[]"
    
    cursor.execute("""
        INSERT INTO agents (agent_id, owner_id, name, description, agent_type, 
                           endpoint_url, avatar_url, tags, namespace)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (agent_id, user["user_id"], data.name, data.description, 
          data.agent_type, data.endpoint_url, data.avatar_url, tags_str, data.namespace))
    
    # 存储用户提供的 Tokens
    token_count = 0
    if data.tokens and len(data.tokens) > 0:
        for token_value in data.tokens:
            if token_value.strip():
                token_id = f"tkn_{uuid.uuid4().hex[:12]}"
                cursor.execute("""
                    INSERT INTO agent_tokens (token_id, agent_id, token_value, permissions,
                                             price_chat, price_read, price_react, namespace)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (token_id, agent_id, token_value.strip(), '["chat","read","react"]',
                      data.price_chat, data.price_read, data.price_react, data.namespace))
                token_count += 1
    
    # 如果没有提供 Token，生成一个默认的
    if token_count == 0:
        token_id = f"tkn_{uuid.uuid4().hex[:12]}"
        token_value = generate_agent_token()
        cursor.execute("""
            INSERT INTO agent_tokens (token_id, agent_id, token_value, permissions,
                                     price_chat, price_read, price_react)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (token_id, agent_id, token_value, '["chat","read","react"]',
              data.price_chat, data.price_read, data.price_react))
        token_count = 1
    
    conn.commit()
    conn.close()
    
    return {
        "agent_id": agent_id,
        "name": data.name,
        "token_count": token_count,
        "message": f"Agent 创建成功，已添加 {token_count} 个 Token"
    }


# ==================== Token 购买 API ====================

@app.post("/api/tokens/purchase")
async def purchase_token(data: TokenPurchase, user: dict = Depends(get_current_user)):
    """购买 Token"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取 Agent 信息
    cursor.execute("""
        SELECT agent_id, owner_id, name FROM agents WHERE agent_id = ?
    """, (data.agent_id,))
    
    agent = cursor.fetchone()
    if not agent:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent 不存在")
    
    # 不能购买自己的 Agent
    if agent["owner_id"] == user["user_id"]:
        conn.close()
        raise HTTPException(status_code=400, detail="不能购买自己的 Agent Token")
    
    # 获取一个可用的 Token（优先取有 unit_price 的）
    cursor.execute("""
        SELECT token_id, token_value, unit_price, scope_label FROM agent_tokens 
        WHERE agent_id = ? AND is_sold = 0
        ORDER BY unit_price DESC
        LIMIT 1
    """, (data.agent_id,))
    
    available_token = cursor.fetchone()
    if not available_token:
        conn.close()
        raise HTTPException(status_code=400, detail="该 Agent 暂无可用 Token")
    
    # 使用 unit_price 作为价格
    total_price = int(available_token["unit_price"] or 0)
    
    # 检查余额
    cursor.execute(
        "SELECT atp_balance FROM users WHERE user_id = ?",
        (user["user_id"],)
    )
    user_data = cursor.fetchone()
    
    if user_data["atp_balance"] < total_price:
        conn.close()
        raise HTTPException(status_code=400, detail=f"ATP 余额不足，需要 {total_price} ATP")
    
    # 扣除买家余额
    cursor.execute(
        "UPDATE users SET atp_balance = atp_balance - ? WHERE user_id = ?",
        (total_price, user["user_id"])
    )
    
    # 增加卖家余额
    cursor.execute(
        "UPDATE users SET atp_balance = atp_balance + ? WHERE user_id = ?",
        (total_price, agent["owner_id"])
    )
    
    buyer_token = available_token["token_value"]
    token_id = available_token["token_id"]
    
    # 标记 Token 为已售
    cursor.execute("""
        UPDATE agent_tokens SET is_sold = 1, sold_to_user_id = ?, sold_at = datetime('now')
        WHERE token_id = ?
    """, (user["user_id"], token_id))
    
    # 记录购买
    purchase_id = f"pur_{uuid.uuid4().hex[:12]}"
    cursor.execute("""
        INSERT INTO purchased_tokens (purchase_id, user_id, agent_id, token_id, 
                                     token_value, permissions, atp_spent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (purchase_id, user["user_id"], data.agent_id, token_id,
          buyer_token, '["chat","read","react"]', total_price))
    
    # 记录交易
    tx_id = f"tx_{uuid.uuid4().hex[:12]}"
    cursor.execute("""
        INSERT INTO transactions (tx_id, from_user_id, to_user_id, agent_id, 
                                 atp_amount, tx_type, description)
        VALUES (?, ?, ?, ?, ?, 'purchase', ?)
    """, (tx_id, user["user_id"], agent["owner_id"], data.agent_id, 
          total_price, f"购买 {agent['name']} Token"))
    
    conn.commit()
    
    # 获取更新后的余额
    cursor.execute(
        "SELECT atp_balance FROM users WHERE user_id = ?",
        (user["user_id"],)
    )
    new_balance = cursor.fetchone()["atp_balance"]
    conn.close()
    
    return {
        "purchase_id": purchase_id,
        "agent_name": agent["name"],
        "token": buyer_token,
        "permissions": ["chat", "read", "react"],
        "atp_spent": total_price,
        "remaining_balance": new_balance
    }


@app.get("/api/tokens/validate")
async def validate_token(token: str, agent_id: str):
    """验证 Token（查询 Agent 的 API 获取详情）"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取 Agent 信息
    cursor.execute("SELECT endpoint_url FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    conn.close()
    
    if not agent:
        return {"valid": False, "error": "Agent 不存在"}
    
    url = agent["endpoint_url"].rstrip('/')
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{url}/api/hub/token/validate", params={"token": token})
            if res.status_code == 200:
                return res.json()
            return {"valid": False, "error": "Token 验证失败"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@app.get("/api/tokens/my")
async def my_tokens(user: dict = Depends(get_current_user)):
    """获取我购买的 Token"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT p.*, a.name as agent_name, a.endpoint_url, a.namespace
        FROM purchased_tokens p
        JOIN agents a ON p.agent_id = a.agent_id
        WHERE p.user_id = ?
        ORDER BY p.created_at DESC
    """, (user["user_id"],))
    
    tokens = []
    for row in cursor.fetchall():
        token = dict(row)
        # 构建完整的访问 URL（包含 token 和 namespace）
        base_url = token.get("endpoint_url", "").rstrip("/")
        ns = token.get("namespace", "default")
        token_value = token.get("token_value", "")
        if ns != "default":
            token["access_url"] = f"{base_url}/?token={token_value}&ns={ns}"
        else:
            token["access_url"] = f"{base_url}/?token={token_value}"
        tokens.append(token)
    
    conn.close()
    
    return {"tokens": tokens, "total": len(tokens)}


# ==================== 交易 API ====================

@app.get("/api/transactions")
async def get_transactions(user: dict = Depends(get_current_user)):
    """获取交易历史"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM transactions 
        WHERE from_user_id = ? OR to_user_id = ?
        ORDER BY created_at DESC
        LIMIT 50
    """, (user["user_id"], user["user_id"]))
    
    txs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"transactions": txs}


@app.get("/api/balance")
async def get_balance(user: dict = Depends(get_current_user)):
    """获取 ATP 余额"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT atp_balance FROM users WHERE user_id = ?",
        (user["user_id"],)
    )
    user_data = cursor.fetchone()
    conn.close()
    
    return {"atp_balance": user_data["atp_balance"]}


# ==================== 统计 API ====================

@app.get("/api/stats")
async def get_stats():
    """获取平台统计"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as count FROM users")
    users = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) as count FROM agents WHERE status = 'active'")
    agents = cursor.fetchone()["count"]
    
    cursor.execute("SELECT SUM(atp_amount) as total FROM transactions WHERE tx_type = 'purchase'")
    result = cursor.fetchone()
    total_traded = result["total"] if result["total"] else 0
    
    conn.close()
    
    return {
        "total_users": users,
        "total_agents": agents,
        "total_atp_traded": total_traded
    }


# ==================== Trending API ====================

@app.get("/api/trending")
async def get_trending():
    """获取热门 Agent 和 Simulation"""
    conn = get_db()
    cursor = conn.cursor()

    # Hot Agents: by purchase count → available tokens → created_at
    cursor.execute("""
        SELECT a.agent_id, a.name, a.agent_type, a.description, a.avatar_url,
               u.username as owner_name,
               COALESCE(tx.purchase_count, 0) as purchase_count,
               COALESCE(tk.available_tokens, 0) as available_tokens
        FROM agents a
        JOIN users u ON a.owner_id = u.user_id
        LEFT JOIN (
            SELECT JSON_EXTRACT(description, '$.agent_id') as agent_id, COUNT(*) as purchase_count
            FROM transactions WHERE tx_type = 'purchase'
            GROUP BY JSON_EXTRACT(description, '$.agent_id')
        ) tx ON tx.agent_id = a.agent_id
        LEFT JOIN (
            SELECT agent_id, COUNT(*) as available_tokens
            FROM agent_tokens WHERE is_sold = 0 AND validated = 1
            GROUP BY agent_id
        ) tk ON tk.agent_id = a.agent_id
        WHERE a.status = 'active'
        ORDER BY purchase_count DESC, available_tokens DESC, a.created_at DESC
        LIMIT 5
    """)
    hot_agents = [dict(row) for row in cursor.fetchall()]

    # Hot Simulations: by participant count → rounds → created_at
    cursor.execute("""
        SELECT s.simulation_id, s.title, s.description, s.status, s.category,
               COALESCE(p.participant_count, 0) as participant_count,
               COALESCE(r.round_count, 0) as round_count
        FROM simulations s
        LEFT JOIN (
            SELECT simulation_id, COUNT(*) as participant_count
            FROM simulation_participants
            GROUP BY simulation_id
        ) p ON p.simulation_id = s.simulation_id
        LEFT JOIN (
            SELECT simulation_id, COUNT(*) as round_count
            FROM simulation_rounds
            GROUP BY simulation_id
        ) r ON r.simulation_id = s.simulation_id
        ORDER BY participant_count DESC, round_count DESC, s.created_at DESC
        LIMIT 5
    """)
    hot_simulations = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return {"hot_agents": hot_agents, "hot_simulations": hot_simulations}


# ==================== 前端路由 ====================

@app.get("/")
async def index():
    """首页"""
    return FileResponse(FRONTEND_PATH / "index.html")


@app.get("/marketplace")
async def marketplace():
    """市场页"""
    return FileResponse(FRONTEND_PATH / "marketplace.html")


@app.get("/dashboard")
async def dashboard():
    """仪表盘"""
    return FileResponse(FRONTEND_PATH / "dashboard.html")


@app.get("/simulation")
async def simulation_page():
    """Simulation 页面"""
    return FileResponse(FRONTEND_PATH / "simulation.html")


@app.get("/guide")
async def guide_page():
    """文档页面"""
    return FileResponse(FRONTEND_PATH / "docs.html")


@app.get("/robots.txt")
async def robots_txt():
    """robots.txt for search engines and AI bots"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("""User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Google-Extended
Allow: /

Sitemap: https://wielding.ai/sitemap.xml
""")


@app.get("/sitemap.xml")
async def sitemap_xml():
    """XML Sitemap"""
    from fastapi.responses import Response
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://wielding.ai/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>
  <url><loc>https://wielding.ai/marketplace</loc><changefreq>daily</changefreq><priority>0.9</priority></url>
  <url><loc>https://wielding.ai/simulation</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>
  <url><loc>https://wielding.ai/guide</loc><changefreq>monthly</changefreq><priority>0.7</priority></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")


# ==================== Simulation Routes ====================

from simulation_routes import router as simulation_router, agent_sim_router
app.include_router(simulation_router)
app.include_router(agent_sim_router)


# 静态资源
app.mount("/static", StaticFiles(directory=FRONTEND_PATH), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
