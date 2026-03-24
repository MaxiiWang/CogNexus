"""
Knowledge API — 从 brain-visual 迁移的知识管理端点

前缀: /api/knowledge/{namespace}/
认证: CogNexus JWT (get_current_user)
权限: namespace 所有者才能访问
"""
import json
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import verify_token as verify_jwt
from database import get_db

# ==================== 路由 & 依赖 ====================

router = APIRouter(prefix="/api/knowledge/{namespace}", tags=["knowledge"])


async def verify_namespace(
    namespace: str,
    authorization: str = Header(None),
):
    """路由级别的 namespace 权限验证依赖"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")

    token = authorization.split(" ")[1]
    payload = verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    user_id = payload.get("user_id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT agent_id FROM agents WHERE owner_id = ? AND namespace = ? LIMIT 1",
        (user_id, namespace)
    )
    agent = cursor.fetchone()
    conn.close()

    if not agent:
        raise HTTPException(status_code=403, detail="无权访问此 namespace")

    return payload


# ==================== 请求模型 ====================

class ChatRequest(BaseModel):
    message: str
    context: Optional[dict] = None


class AskRequest(BaseModel):
    question: str
    max_sources: int = 5


class PrivacyRequest(BaseModel):
    entity_id: str
    is_private: bool
    cascade: bool = False


class ActionRequest(BaseModel):
    action: str
    params: dict


# ==================== 辅助函数 ====================

def _check_cogmate_available():
    """检查 cogmate 是否可用"""
    try:
        import cogmate_core
        return True
    except ImportError:
        return False

def _get_cogmate(namespace: str):
    """获取指定 namespace 的 CogmateAgent 实例"""
    if not _check_cogmate_available():
        raise HTTPException(status_code=503, detail="知识服务未配置，请先部署 Cogmate 基础设施")
    from cogmate_core import CogmateAgent
    return CogmateAgent(namespace=namespace)


def _get_agent_llm_config(namespace: str) -> dict:
    """获取 Agent 的 LLM 配置（从 agents 表的 llm_config 字段）"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT llm_config FROM agents WHERE namespace = ?", (namespace,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            import json as _json
            config = _json.loads(row[0]) if isinstance(row[0], str) else row[0]
            return config if isinstance(config, dict) else {}
        return {}
    except Exception:
        return {}


def _get_cogmate_sqlite(namespace: str = "default"):
    """获取 cogmate 的 SQLite 连接（用于 timeline、隐私过滤等）"""
    from cogmate_core import get_sqlite
    return get_sqlite()


def _get_private_fact_ids(namespace: str = "default") -> set:
    """获取所有私有 fact_id"""
    conn = _get_cogmate_sqlite(namespace)
    cursor = conn.cursor()
    cursor.execute("SELECT fact_id FROM facts WHERE is_private = 1")
    private_ids = set(r[0] for r in cursor.fetchall())
    conn.close()
    return private_ids


def _get_private_abstract_ids(namespace: str = "default") -> set:
    """获取所有私有 abstract_id"""
    conn = _get_cogmate_sqlite(namespace)
    cursor = conn.cursor()
    cursor.execute("SELECT abstract_id FROM abstracts WHERE is_private = 1")
    private_ids = set(r[0] for r in cursor.fetchall())
    conn.close()
    return private_ids


# ==================== 图谱端点 ====================

@router.get("/graph")
async def get_graph(
    namespace: str,
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(verify_namespace),
):
    """获取知识图谱数据"""
    from cogmate_core import get_neo4j

    driver = get_neo4j()
    nodes = []
    edges = []

    with driver.session() as session:
        node_result = session.run('''
            MATCH (f:Fact)
            WHERE f.namespace = $ns OR ($ns = "default" AND f.namespace IS NULL)
            OPTIONAL MATCH (f)-[r]-()
            WITH f, count(r) as degree
            RETURN f.fact_id as id, f.summary as label,
                   f.content_type as type, f.timestamp as timestamp,
                   degree
            ORDER BY f.timestamp DESC
            SKIP $offset LIMIT $limit
        ''', ns=namespace, offset=offset, limit=limit)

        for record in node_result:
            full_label = record["label"] or ""
            nodes.append({
                "id": record["id"],
                "label": full_label[:50] + ("..." if len(full_label) > 50 else ""),
                "full_content": full_label,
                "type": record["type"],
                "timestamp": record["timestamp"],
                "degree": record["degree"],
            })

        edge_result = session.run('''
            MATCH (a:Fact)-[r]->(b:Fact)
            WHERE (a.namespace = $ns OR ($ns = "default" AND a.namespace IS NULL))
            RETURN a.fact_id as source, b.fact_id as target,
                   type(r) as type, r.confidence as confidence
        ''', ns=namespace)

        for record in edge_result:
            edges.append({
                "source": record["source"],
                "target": record["target"],
                "type": record["type"],
                "confidence": record["confidence"],
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {"total_nodes": len(nodes), "total_edges": len(edges)},
    }


@router.get("/graph/node/{node_id}")
async def get_node(
    namespace: str,
    node_id: str,
    user: dict = Depends(verify_namespace),
):
    """获取单个节点详情"""
    cogmate = _get_cogmate(namespace)
    fact = cogmate.get_fact(node_id)

    if not fact:
        raise HTTPException(status_code=404, detail="节点未找到")

    results = cogmate.query(fact.get("summary", ""), top_k=5)

    return {
        "node": fact,
        "relations": results.get("graph_results", []),
    }


# ==================== 树状图 ====================

@router.get("/tree")
async def get_tree(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """获取抽象层树形结构"""
    from cogmate_core.abstraction import list_abstracts

    abstracts = list_abstracts(namespace=namespace)

    return {
        "abstracts": [
            {
                "id": a["abstract_id"][:8],
                "name": a["name"],
                "description": (a["description"] or "")[:200],
                "status": a["status"],
                "source_count": len(a["source_fact_ids"]),
                "source_facts": a["source_fact_ids"][:10],
            }
            for a in abstracts
        ],
    }


# ==================== 时间线 ====================

@router.get("/timeline")
async def get_timeline(
    namespace: str,
    start: str = Query(None),
    end: str = Query(None),
    granularity: str = Query("day"),
    user: dict = Depends(verify_namespace),
):
    """获取时间线数据"""
    conn = _get_cogmate_sqlite(namespace)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT fact_id, summary, content_type, timestamp, created_at
        FROM facts
        WHERE namespace = ? OR (? = 'default' AND (namespace IS NULL OR namespace = 'default'))
        ORDER BY created_at DESC
    ''', (namespace, namespace))

    facts = []
    for row in cursor.fetchall():
        full_label = row[1] or ""
        facts.append({
            "id": row[0][:8],
            "full_id": row[0],
            "label": full_label[:50] + ("..." if len(full_label) > 50 else ""),
            "full_content": full_label,
            "type": row[2],
            "timestamp": row[3],
            "created_at": row[4],
        })

    conn.close()

    return {
        "facts": facts,
        "granularity": granularity,
    }


# ==================== 搜索 ====================

@router.get("/search")
async def search(
    namespace: str,
    q: str = Query(..., min_length=1),
    user: dict = Depends(verify_namespace),
):
    """知识语义搜索"""
    cogmate = _get_cogmate(namespace)
    results = cogmate.query(q, top_k=20)

    vector_results = results.get("vector_results", [])[:10]

    return {
        "query": q,
        "results": vector_results,
        "total": len(vector_results),
    }


# ==================== 对话 ====================

@router.post("/chat")
async def chat(
    namespace: str,
    request: ChatRequest,
    user: dict = Depends(verify_namespace),
):
    """知识对话（内部管理用，支持 slash 命令）"""
    from cogmate_core.intent_handler import IntentHandler

    handler = IntentHandler(namespace=namespace)
    response = handler.process(request.message)

    return {
        "response": response,
        "context": request.context,
    }


@router.get("/chat/stream")
async def chat_stream(
    namespace: str,
    q: str = Query(..., description="对话消息"),
    user: dict = Depends(verify_namespace),
):
    """流式对话端点（SSE）— 支持 slash 命令 + LLM 流式输出"""

    async def event_stream():
        message = q.strip()

        # Slash 命令不流式，直接返回
        if message.startswith('/'):
            from cogmate_core.intent_handler import IntentHandler
            handler = IntentHandler(namespace=namespace)
            result = handler._handle_slash_command(message)
            yield f"data: {json.dumps({'type': 'content', 'text': result})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # 检索知识库
        cogmate = _get_cogmate(namespace)
        results = cogmate.query(query_text=message, top_k=5, min_score=0.5)
        vector_results = results.get("vector_results", [])

        yield f"data: {json.dumps({'type': 'meta', 'sources_count': len(vector_results)})}\n\n"

        if not vector_results:
            yield f"data: {json.dumps({'type': 'content', 'text': '📭 知识库中暂无相关内容。'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # 流式 LLM 生成
        try:
            from cogmate_core.llm_answer import generate_answer
            llm_cfg = _get_agent_llm_config(namespace)
            stream_gen = generate_answer(
                message, vector_results, stream=True,
                namespace=namespace,
                override_api_key=llm_cfg.get("api_key"),
                override_model=llm_cfg.get("model"),
                override_provider=llm_cfg.get("provider"),
                override_endpoint=llm_cfg.get("endpoint"),
            )
            for chunk in stream_gen:
                yield f"data: {json.dumps({'type': 'content', 'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ==================== 统计 & 健康 ====================

@router.get("/stats")
async def get_stats(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """获取知识库统计概览"""
    cogmate = _get_cogmate(namespace)
    stats = cogmate.stats()

    return {
        "total_facts": stats["total_facts"],
        "graph_nodes": stats["graph_nodes"],
        "graph_edges": stats["graph_edges"],
        "by_type": stats.get("by_type", {}),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/health")
async def get_health(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """获取知识图谱健康度"""
    from cogmate_core.graph_health import get_graph_metrics, evaluate_health

    metrics = get_graph_metrics()
    health = evaluate_health(metrics)

    return {"metrics": metrics, "health": health}


# ==================== 问答 API ====================

@router.post("/ask")
async def ask(
    namespace: str,
    request: AskRequest,
    user: dict = Depends(verify_namespace),
):
    """知识问答服务"""
    cogmate = _get_cogmate(namespace)

    # 语义搜索
    results = cogmate.query(
        query_text=request.question,
        top_k=request.max_sources * 2,
        min_score=0.5,
    )

    vector_results = results.get("vector_results", [])[:request.max_sources]

    if not vector_results:
        return {
            "answer": "抱歉，在知识库中没有找到相关信息。",
            "sources_count": 0,
        }

    # 使用 LLM 生成回答（支持 per-Agent 配置）
    from cogmate_core.llm_answer import generate_answer
    llm_cfg = _get_agent_llm_config(namespace)
    answer = generate_answer(
        request.question, vector_results,
        namespace=namespace,
        override_api_key=llm_cfg.get("api_key"),
        override_model=llm_cfg.get("model"),
        override_provider=llm_cfg.get("provider"),
        override_endpoint=llm_cfg.get("endpoint"),
    )

    return {
        "answer": answer,
        "sources_count": len(vector_results),
    }


@router.get("/ask/stream")
async def ask_stream(
    namespace: str,
    q: str = Query(..., description="问题"),
    user: dict = Depends(verify_namespace),
):
    """流式问答 API（Server-Sent Events）"""
    cogmate = _get_cogmate(namespace)

    # 语义搜索
    results = cogmate.query(query_text=q, top_k=10, min_score=0.5)
    vector_results = results.get("vector_results", [])[:5]

    async def event_stream():
        # 发送元数据
        yield f"data: {json.dumps({'type': 'meta', 'sources_count': len(vector_results)})}\n\n"

        if not vector_results:
            yield f"data: {json.dumps({'type': 'content', 'text': '抱歉，在知识库中没有找到相关信息。'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # 流式生成回答（支持 per-Agent 配置）
        try:
            from cogmate_core.llm_answer import generate_answer
            llm_cfg = _get_agent_llm_config(namespace)
            stream_gen = generate_answer(
                q, vector_results, stream=True,
                namespace=namespace,
                override_api_key=llm_cfg.get("api_key"),
                override_model=llm_cfg.get("model"),
                override_provider=llm_cfg.get("provider"),
                override_endpoint=llm_cfg.get("endpoint"),
            )
            for chunk in stream_gen:
                yield f"data: {json.dumps({'type': 'content', 'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/ask/stats")
async def ask_stats(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """查询问答统计"""
    cogmate = _get_cogmate(namespace)
    stats = cogmate.stats()

    return {
        "namespace": namespace,
        "total_facts": stats.get("total_facts", 0),
        "timestamp": datetime.now().isoformat(),
    }


# ==================== 隐私控制 ====================

@router.post("/privacy")
async def set_privacy(
    namespace: str,
    request: PrivacyRequest,
    user: dict = Depends(verify_namespace),
):
    """设置实体隐私状态"""
    from cogmate_core.privacy import (
        set_fact_private, set_abstract_private, get_privacy_status,
    )

    status = get_privacy_status(request.entity_id)
    if not status:
        raise HTTPException(status_code=404, detail="实体未找到")

    if status["type"] == "fact":
        success = set_fact_private(request.entity_id, request.is_private)
        return {
            "success": success,
            "entity_type": "fact",
            "entity_id": status["id"],
            "is_private": request.is_private,
        }
    else:
        success, affected = set_abstract_private(
            request.entity_id,
            request.is_private,
            cascade=request.cascade,
        )
        return {
            "success": success,
            "entity_type": "abstract",
            "entity_id": status["id"],
            "is_private": request.is_private,
            "cascade": request.cascade,
            "affected_facts": len(affected) if affected else 0,
        }


@router.get("/privacy/{entity_id}")
async def get_privacy(
    namespace: str,
    entity_id: str,
    user: dict = Depends(verify_namespace),
):
    """获取实体隐私状态"""
    from cogmate_core.privacy import get_privacy_status

    status = get_privacy_status(entity_id)
    if not status:
        raise HTTPException(status_code=404, detail="实体未找到")

    return status


@router.get("/privacy-stats")
async def get_privacy_stats(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """获取隐私统计"""
    from cogmate_core.privacy import get_privacy_stats as _get_stats
    return _get_stats()


# ==================== 操作动作 ====================

@router.post("/action")
async def action(
    namespace: str,
    request: ActionRequest,
    user: dict = Depends(verify_namespace),
):
    """执行知识图谱操作"""
    cogmate = _get_cogmate(namespace)

    if request.action == "create_relation":
        params = request.params
        result = cogmate.create_relation(
            params["from_id"],
            params["to_id"],
            params.get("relation_type", "RELATES_TO"),
            params.get("confidence", 3),
        )
        return {"success": True, "result": result}

    raise HTTPException(status_code=400, detail=f"未知操作: {request.action}")


# ==================== Profile / Persona 管理 ====================

class ProfileUpdate(BaseModel):
    """Profile 更新请求"""
    identity: Optional[dict] = None   # {name, title, bio, avatar}
    persona: Optional[dict] = None    # {based_on, era, background, traits, speaking_style, core_beliefs, ...}
    preferences: Optional[dict] = None
    llm: Optional[dict] = None        # {provider, model, api_key, endpoint}


@router.get("/profile")
async def get_profile(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """获取 Agent 的 Profile 配置（含 persona）"""
    try:
        from cogmate_core.profile_manager import ProfileManager
        pm = ProfileManager()
        config = pm.load_profile_config(namespace)
        if config:
            # 脱敏 LLM key
            llm = config.get("llm", {})
            if llm.get("api_key"):
                key = llm["api_key"]
                llm["api_key_masked"] = key[:6] + "..." + key[-4:] if len(key) > 10 else "***"
                llm["has_key"] = True
                del llm["api_key"]
            else:
                llm["has_key"] = False
            config["llm"] = llm
            return config
        return {"namespace": namespace, "type": "human", "identity": {}, "persona": {}, "llm": {"has_key": False}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载 Profile 失败: {str(e)}")


@router.put("/profile")
async def update_profile(
    namespace: str,
    data: ProfileUpdate,
    user: dict = Depends(verify_namespace),
):
    """更新 Agent 的 Profile 配置（含 persona）"""
    try:
        from cogmate_core.profile_manager import ProfileManager
        pm = ProfileManager()
        config = pm.load_profile_config(namespace)
        if not config:
            config = {"namespace": namespace, "type": "human"}

        # 合并更新
        if data.identity:
            config["identity"] = {**config.get("identity", {}), **data.identity}
        if data.persona:
            config["persona"] = {**config.get("persona", {}), **data.persona}
        if data.preferences:
            config["preferences"] = {**config.get("preferences", {}), **data.preferences}
        if data.llm:
            existing_llm = config.get("llm", {})
            new_llm = data.llm.copy()
            # 如果没传 api_key，保留已有的
            if not new_llm.get("api_key") and existing_llm.get("api_key"):
                new_llm["api_key"] = existing_llm["api_key"]
            config["llm"] = {**existing_llm, **new_llm}

        # 同步更新 agents 表的基本信息
        if data.identity:
            conn = get_db()
            updates = []
            params = []
            if "name" in data.identity:
                updates.append("name = ?")
                params.append(data.identity["name"])
            if "bio" in data.identity:
                updates.append("description = ?")
                params.append(data.identity["bio"])
            if "avatar" in data.identity:
                updates.append("avatar_url = ?")
                params.append(data.identity["avatar"])
            if updates:
                params.append(namespace)
                conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE namespace = ?", params)
                conn.commit()
            conn.close()

        # 同步 LLM 配置到 agents 表
        if data.llm:
            import json as _json
            llm_for_db = {k: v for k, v in config.get("llm", {}).items()}
            conn = get_db()
            conn.execute("UPDATE agents SET llm_config = ? WHERE namespace = ?",
                         (_json.dumps(llm_for_db), namespace))
            conn.commit()
            conn.close()

        # 保存到 JSON 文件
        pm.save_profile_config(namespace, config)

        return {"success": True, "message": "Profile 已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新 Profile 失败: {str(e)}")


# ==================== Character 自动调研 ====================

class ResearchRequest(BaseModel):
    reference_names: list  # 参考人物名列表, e.g. ["Elon Musk"]
    depth: str = "normal"  # normal | deep


@router.post("/research-character")
async def research_character_endpoint(
    namespace: str,
    request: ResearchRequest,
    user: dict = Depends(verify_namespace),
):
    """对 Character Agent 进行自动化调研，填充 Persona 和知识库"""
    # 验证是 character 类型
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT agent_type, name, llm_config FROM agents WHERE namespace = ?", (namespace,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if row[0] != "character":
        raise HTTPException(status_code=400, detail="仅 Character 类型 Agent 支持自动调研")

    agent_name = row[1]
    llm_config = {}
    if row[2]:
        try:
            llm_config = json.loads(row[2])
        except:
            pass

    if not llm_config.get("api_key"):
        raise HTTPException(status_code=400, detail="请先在 Config 中配置 LLM API Key")

    try:
        from cogmate_core.character_research import (
            research_character, apply_persona_to_profile, store_initial_knowledge,
            discover_relations_with_llm, generate_abstracts_with_llm
        )

        # 1. 搜索 + 生成 Persona
        result = research_character(
            character_names=request.reference_names,
            depth=request.depth,
            llm_config=llm_config,
            agent_name=agent_name,
        )

        persona = result.get("persona")
        if not persona:
            raise HTTPException(status_code=500, detail="调研失败: 未生成 Persona")

        # 2. 应用 Persona 到 Profile
        apply_persona_to_profile(namespace, persona, request.reference_names)

        # 3. 存储知识到三库
        stored = store_initial_knowledge(namespace, persona)

        # 4. LLM 分析关联关系 → 自动创建 Graph 边
        relations_created = discover_relations_with_llm(
            namespace, llm_config=llm_config, agent_name=agent_name)

        # 5. LLM 归纳总结 → 自动生成 Tree 抽象层
        abstracts_created = generate_abstracts_with_llm(
            namespace, llm_config=llm_config, agent_name=agent_name)

        return {
            "success": True,
            "persona_summary": {
                "era": persona.era,
                "traits": persona.traits,
                "speaking_style": persona.speaking_style[:100],
                "core_beliefs_count": len(persona.core_beliefs),
                "quotes_count": len(persona.famous_quotes),
            },
            "knowledge_stored": stored,
            "relations_created": relations_created,
            "abstracts_created": abstracts_created,
            "sources": result.get("sources", []),
            "message": f"调研完成。已存储 {stored} 条知识、创建 {relations_created} 条关联、生成 {abstracts_created} 个抽象主题。"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"调研失败: {str(e)}")


# ==================== 公开 Token 认证 (访客页面) ====================

public_router = APIRouter(prefix="/api/knowledge", tags=["knowledge-public"])


async def _verify_agent_token(token: str):
    """验证 agent_token 并返回 (agent_id, namespace, scope, token_row)"""
    if not token:
        raise HTTPException(status_code=401, detail="Token 未提供")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.*, a.namespace, a.name as agent_name, a.agent_id
        FROM agent_tokens t
        JOIN agents a ON t.agent_id = a.agent_id
        WHERE t.token_value = ?
    """, (token,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Token 无效")

    # 检查过期
    if row["expires_at"]:
        from datetime import datetime
        try:
            exp = datetime.fromisoformat(row["expires_at"])
            if exp < datetime.now():
                raise HTTPException(status_code=401, detail="Token 已过期")
        except ValueError:
            pass

    return dict(row)


@public_router.get("/token/verify")
async def verify_agent_token_endpoint(token: str):
    """公开端点：验证 Agent Token 并返回权限信息"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.*, a.namespace, a.name as agent_name, a.agent_id
        FROM agent_tokens t
        JOIN agents a ON t.agent_id = a.agent_id
        WHERE t.token_value = ?
    """, (token,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {"valid": False, "error": "token_not_found"}

    row = dict(row)

    # 检查过期
    if row.get("expires_at"):
        try:
            exp = datetime.fromisoformat(row["expires_at"])
            if exp < datetime.now():
                return {"valid": False, "error": "token_expired"}
        except ValueError:
            pass

    scope = row.get("scope", "unknown")
    permissions = {
        "chat": scope in ("full", "qa_public"),
        "browse": scope in ("full", "browse_public"),
        "full_access": scope == "full",
    }

    qa_limit = row.get("qa_limit", 0)
    qa_used = row.get("qa_used", 0)
    qa_stats = {
        "limit": qa_limit,
        "used": qa_used,
        "remaining": max(0, qa_limit - qa_used) if qa_limit else 0,
        "unlimited": qa_limit == 0,
    }

    return {
        "valid": True,
        "scope": scope,
        "scope_label": row.get("scope_label", ""),
        "permissions": permissions,
        "expires_at": row.get("expires_at"),
        "qa_stats": qa_stats,
        "namespace": row.get("namespace"),
        "agent_id": row.get("agent_id"),
        "agent_name": row.get("agent_name"),
    }


@public_router.get("/public/{namespace}/graph")
async def public_graph(
    namespace: str,
    token: str = Query(...),
    limit: int = Query(300, ge=1, le=1000),
):
    """公开图谱端点（Agent Token 认证）"""
    info = await _verify_agent_token(token)
    scope = info.get("scope", "")
    if scope not in ("full", "browse_public"):
        raise HTTPException(status_code=403, detail="无浏览权限")
    if info.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="Token 与 namespace 不匹配")

    from cogmate_core import get_neo4j
    driver = get_neo4j()
    nodes, edges = [], []

    with driver.session() as session:
        # 获取私有 fact_ids 用于过滤（非 full 权限）
        private_ids = set()
        if scope != "full":
            private_ids = _get_private_fact_ids(namespace)

        node_result = session.run('''
            MATCH (f:Fact)
            WHERE f.namespace = $ns OR ($ns = "default" AND f.namespace IS NULL)
            OPTIONAL MATCH (f)-[r]-()
            WITH f, count(r) as degree
            RETURN f.fact_id as id, f.summary as label,
                   f.content_type as type, f.timestamp as timestamp, degree
            ORDER BY f.timestamp DESC
            LIMIT $limit
        ''', ns=namespace, limit=limit)

        for record in node_result:
            if record["id"] in private_ids:
                continue
            full_label = record["label"] or ""
            nodes.append({
                "id": record["id"],
                "label": full_label[:50] + ("..." if len(full_label) > 50 else ""),
                "full_content": full_label,
                "type": record["type"],
                "timestamp": record["timestamp"],
                "degree": record["degree"],
            })

        node_ids = {n["id"] for n in nodes}
        edge_result = session.run('''
            MATCH (a:Fact)-[r]->(b:Fact)
            WHERE (a.namespace = $ns OR ($ns = "default" AND a.namespace IS NULL))
            RETURN a.fact_id as source, b.fact_id as target,
                   type(r) as type, r.confidence as confidence
        ''', ns=namespace)

        for record in edge_result:
            if record["source"] in node_ids and record["target"] in node_ids:
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "type": record["type"],
                    "confidence": record["confidence"],
                })

    return {"nodes": nodes, "edges": edges, "stats": {"total_nodes": len(nodes), "total_edges": len(edges)}}


@public_router.get("/public/{namespace}/tree")
async def public_tree(namespace: str, token: str = Query(...)):
    """公开树状图端点"""
    info = await _verify_agent_token(token)
    scope = info.get("scope", "")
    if scope not in ("full", "browse_public"):
        raise HTTPException(status_code=403, detail="无浏览权限")
    if info.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="Token 与 namespace 不匹配")

    from cogmate_core.abstraction import list_abstracts
    abstracts = list_abstracts(namespace=namespace)

    return {
        "abstracts": [
            {
                "id": a["abstract_id"][:8],
                "name": a["name"],
                "description": (a["description"] or "")[:200],
                "status": a["status"],
                "source_count": len(a["source_fact_ids"]),
                "source_facts": a["source_fact_ids"][:10],
            }
            for a in abstracts
        ],
    }


@public_router.get("/public/{namespace}/timeline")
async def public_timeline(namespace: str, token: str = Query(...)):
    """公开时间线端点"""
    info = await _verify_agent_token(token)
    scope = info.get("scope", "")
    if scope not in ("full", "browse_public"):
        raise HTTPException(status_code=403, detail="无浏览权限")
    if info.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="Token 与 namespace 不匹配")

    conn = _get_cogmate_sqlite(namespace)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT fact_id, summary, content_type, timestamp, created_at
        FROM facts
        WHERE namespace = ? OR (? = 'default' AND (namespace IS NULL OR namespace = 'default'))
        ORDER BY created_at DESC
    ''', (namespace, namespace))

    private_ids = set()
    if scope != "full":
        private_ids = _get_private_fact_ids(namespace)

    facts = []
    for row in cursor.fetchall():
        if row[0] in private_ids:
            continue
        full_label = row[1] or ""
        facts.append({
            "id": row[0][:8],
            "full_id": row[0],
            "label": full_label[:50] + ("..." if len(full_label) > 50 else ""),
            "full_content": full_label,
            "type": row[2],
            "timestamp": row[3],
            "created_at": row[4],
        })
    conn.close()

    return {"facts": facts, "granularity": "day"}


@public_router.get("/public/{namespace}/stats")
async def public_stats(namespace: str, token: str = Query(...)):
    """公开统计端点"""
    info = await _verify_agent_token(token)
    if info.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="Token 与 namespace 不匹配")

    cogmate = _get_cogmate(namespace)
    stats = cogmate.stats()

    return {
        "total_facts": stats["total_facts"],
        "graph_nodes": stats["graph_nodes"],
        "graph_edges": stats["graph_edges"],
        "by_type": stats.get("by_type", {}),
        "timestamp": datetime.now().isoformat(),
    }


@public_router.post("/public/{namespace}/chat")
async def public_chat(namespace: str, request: ChatRequest, token: str = Query(...)):
    """公开对话端点（Agent Token 认证）"""
    info = await _verify_agent_token(token)
    scope = info.get("scope", "")
    if scope not in ("full", "qa_public"):
        raise HTTPException(status_code=403, detail="无问答权限")
    if info.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="Token 与 namespace 不匹配")

    # 检查问答次数
    qa_limit = info.get("qa_limit", 0)
    qa_used = info.get("qa_used", 0)
    if qa_limit > 0 and qa_used >= qa_limit:
        raise HTTPException(status_code=429, detail="问答次数已用完")

    # 递增 qa_used
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE agent_tokens SET qa_used = qa_used + 1 WHERE token_id = ?",
        (info["token_id"],)
    )
    conn.commit()
    conn.close()

    from cogmate_core.intent_handler import IntentHandler
    handler = IntentHandler(namespace=namespace)
    response = handler.process(request.message)

    return {"response": response, "context": request.context}


@public_router.get("/public/{namespace}/ask/stream")
async def public_ask_stream(namespace: str, q: str = Query(...), token: str = Query(...)):
    """公开流式问答端点"""
    info = await _verify_agent_token(token)
    scope = info.get("scope", "")
    if scope not in ("full", "qa_public"):
        raise HTTPException(status_code=403, detail="无问答权限")
    if info.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="Token 与 namespace 不匹配")

    qa_limit = info.get("qa_limit", 0)
    qa_used = info.get("qa_used", 0)
    if qa_limit > 0 and qa_used >= qa_limit:
        raise HTTPException(status_code=429, detail="问答次数已用完")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE agent_tokens SET qa_used = qa_used + 1 WHERE token_id = ?",
        (info["token_id"],)
    )
    conn.commit()
    conn.close()

    cogmate = _get_cogmate(namespace)
    results = cogmate.query(query_text=q, top_k=10, min_score=0.5)
    vector_results = results.get("vector_results", [])[:5]

    async def event_stream():
        yield f"data: {json.dumps({'type': 'meta', 'sources_count': len(vector_results)})}\n\n"
        if not vector_results:
            yield f"data: {json.dumps({'type': 'content', 'text': '抱歉，在知识库中没有找到相关信息。'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return
        try:
            from cogmate_core.llm_answer import generate_answer
            llm_cfg = _get_agent_llm_config(namespace)
            stream_gen = generate_answer(
                q, vector_results, stream=True,
                namespace=namespace,
                override_api_key=llm_cfg.get("api_key"),
                override_model=llm_cfg.get("model"),
                override_provider=llm_cfg.get("provider"),
                override_endpoint=llm_cfg.get("endpoint"),
            )
            for chunk in stream_gen:
                yield f"data: {json.dumps({'type': 'content', 'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
