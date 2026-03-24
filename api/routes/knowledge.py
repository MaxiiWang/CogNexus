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
    """路由级别的 namespace 权限验证依赖
    
    所有者：完全访问
    其他已登录用户：仅限公开 Agent 的对话和统计（不能直接读取知识库原文）
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")

    token = authorization.split(" ")[1]
    payload = verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    user_id = payload.get("user_id")

    conn = get_db()
    cursor = conn.cursor()

    # 先检查是否是所有者
    cursor.execute(
        "SELECT agent_id FROM agents WHERE owner_id = ? AND namespace = ? LIMIT 1",
        (user_id, namespace)
    )
    if cursor.fetchone():
        conn.close()
        payload["_is_owner"] = True
        return payload

    # 非所有者：检查该 namespace 对应的 Agent 是否公开
    cursor.execute(
        "SELECT agent_id, is_public FROM agents WHERE namespace = ? LIMIT 1",
        (namespace,)
    )
    agent = cursor.fetchone()
    conn.close()

    if agent and agent["is_public"] == 1:
        payload["_is_owner"] = False
        return payload

    raise HTTPException(status_code=403, detail="无权访问此 namespace")


def _require_public_chat_only(user: dict):
    """非所有者只能使用对话和统计，不能直接读取知识库原文"""
    if not user.get("_is_owner"):
        raise HTTPException(status_code=403, detail="知识库详情仅所有者可访问，请通过对话功能提问")


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


class FactUpdateRequest(BaseModel):
    summary: str
    content_type: Optional[str] = None


class PrivacyBatchRequest(BaseModel):
    entity_ids: list
    is_private: bool


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


def _require_owner(user: dict):
    """在写操作中调用，确保当前用户是 namespace 所有者"""
    if not user.get("_is_owner"):
        raise HTTPException(status_code=403, detail="仅所有者可执行此操作")


def _get_agent_info(namespace: str) -> dict:
    """获取 namespace 对应的 Agent 基本信息（type、name、description）"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT agent_type, name, description FROM agents WHERE namespace = ?",
            (namespace,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"agent_type": row[0], "name": row[1], "description": row[2]}
    except Exception:
        pass
    return {"agent_type": "human", "name": "", "description": ""}


def _get_chat_config(namespace: str) -> dict:
    """获取 Agent 的对话行为配置"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_config FROM agents WHERE namespace = ?", (namespace,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except Exception:
        pass
    return {}


def _build_dynamic_system_prompt(namespace: str, vector_results: list, chat_config: dict, context_messages: list = None) -> str:
    """根据 chat_config 动态构建 system prompt"""
    parts = []
    
    # 1. 加载 Persona
    try:
        from cogmate_core.profile_manager import ProfileManager
        pm = ProfileManager()
        profile = pm.load_profile_config(namespace)
        if profile:
            p = profile.get("persona", {})
            identity = profile.get("identity", {})
            if identity.get("name"):
                parts.append(f"你是{identity['name']}。")
            if p.get("background"):
                parts.append(p["background"])
            if p.get("speaking_style"):
                parts.append(f"说话风格：{p['speaking_style']}")
            if p.get("core_beliefs"):
                parts.append("核心信念：" + "；".join(p["core_beliefs"][:5]))
    except Exception:
        pass
    
    if not parts:
        agent_info = _get_agent_info(namespace)
        name = agent_info.get("name", "AI助手")
        desc = agent_info.get("description", "")
        parts.append(f"你是{name}。{desc}")
    
    # 2. 认知画像 / Voice Profile
    voice = chat_config.get("voice_profile", "")
    if voice:
        parts.append(f"\n回答风格：{voice}")
    
    # 3. 回答语气
    tone = chat_config.get("tone", "")
    tone_map = {
        "formal": "请使用正式、专业的语气回答。",
        "friendly": "请使用友好、亲切的语气回答。",
        "academic": "请使用学术、严谨的语气回答，适当引用来源。",
        "roleplay": "请完全以角色身份说话，保持角色一致性。",
    }
    if tone and tone in tone_map:
        parts.append(tone_map[tone])
    
    # 4. 高维视角
    if chat_config.get("enable_meta_thinking"):
        parts.append("\n📐 高维视角规则：约 1/3 的回答中，在自然相关时附加一条高维视角洞察，来源于哲学、认知科学、复杂系统、信息论等领域。以「📐 高维视角:」为前缀。原则：不牵强附会，宁缺毋滥。")
    
    # 5. 推理链
    if chat_config.get("enable_reasoning_chain"):
        parts.append("\n🧠 推理链规则：当回答涉及多条知识的交叉推理时，展示推理链：\n🧠 [知识A] × [知识B] → [推理方向和关键逻辑]\n存疑点：[最薄弱环节]\n仅在需要跨知识推理时触发，简单问题不需要。")
    
    # 6. 矛盾检测
    if chat_config.get("enable_contradiction"):
        parts.append("\n⚡ 矛盾检测规则：如果检索到的知识之间存在矛盾、张力或不一致，主动指出并分析。矛盾不等于错误，矛盾是认知复杂性的体现。分类：机会vs风险 | 立场冲突 | 时间矛盾 | 条件矛盾。")
    
    # 7. 知识上下文
    if vector_results:
        context_parts = []
        for i, fact in enumerate(vector_results, 1):
            fact_type = fact.get('content_type', '信息')
            summary = fact.get('summary', '')
            context_parts.append(f"[{i}][{fact_type}] {summary}")
        parts.append(f"\n知识库参考（基于这些知识回答，不要简单罗列，要整合分析）：\n" + "\n".join(context_parts))
    
    # 8. 通用规则
    max_tokens = chat_config.get("max_tokens", 2000)
    parts.append(f"\n回答要求：简洁自然，有洞察，不超过约{max_tokens}字。如果知识库中没有直接相关内容，坦诚说明。")
    
    return "\n".join(parts)


def _graph_reasoning(namespace: str, fact_ids: list, max_hops: int = 2) -> list:
    """通过 Neo4j 图谱查询关联知识，扩展检索结果"""
    try:
        from cogmate_core import get_neo4j
        driver = get_neo4j()
        extra_facts = []
        seen_ids = set(fact_ids)

        with driver.session() as session:
            for fid in fact_ids[:5]:  # 只对 top 5 做扩展，避免太慢
                result = session.run("""
                    MATCH (a:Fact {fact_id: $fid})-[r]-(b:Fact)
                    WHERE b.namespace = $ns OR ($ns = "default" AND b.namespace IS NULL)
                    RETURN b.fact_id as id, b.summary as summary, b.content_type as type,
                           type(r) as relation, r.confidence as confidence
                    LIMIT 3
                """, fid=fid, ns=namespace)

                for record in result:
                    if record["id"] not in seen_ids:
                        seen_ids.add(record["id"])
                        extra_facts.append({
                            "fact_id": record["id"],
                            "summary": record["summary"],
                            "content_type": record["type"],
                            "relation": record["relation"],
                            "confidence": record["confidence"],
                            "_source": "graph",
                        })

        return extra_facts
    except Exception as e:
        return []


def _web_search_fallback(query: str, max_results: int = 3) -> list:
    """使用 Brave Search API 搜索网络作为知识补充"""
    import os
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return []

    try:
        import httpx
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": max_results, "freshness": "pw"},  # past week
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append({
                "summary": f"[网络] {item.get('title', '')}: {item.get('description', '')}",
                "content_type": "资讯",
                "fact_id": "web_" + item.get("url", "")[:20],
                "_source": "web",
            })
        return results
    except Exception:
        return []


def _safe_query(namespace: str, query_text: str, top_k: int = 5, min_score: float = 0.5) -> dict:
    """安全查询 — 空知识库/不存在的 collection 返回空结果而非崩溃"""
    try:
        cogmate = _get_cogmate(namespace)
        return cogmate.query(query_text=query_text, top_k=top_k, min_score=min_score)
    except Exception:
        return {"vector_results": [], "graph_results": []}


def _safe_stats(namespace: str) -> dict:
    """安全统计 — 空知识库返回零值"""
    try:
        cogmate = _get_cogmate(namespace)
        return cogmate.stats()
    except Exception:
        return {"total_facts": 0, "graph_nodes": 0, "graph_edges": 0, "by_type": {}}


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
    _require_public_chat_only(user)
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
    _require_public_chat_only(user)
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
    _require_public_chat_only(user)
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
    _require_public_chat_only(user)
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
    _require_public_chat_only(user)
    results = _safe_query(namespace, q, top_k=20)

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
    session_id: str = Query(None, description="会话ID（可选，传入则保存消息+使用上下文）"),
    user: dict = Depends(verify_namespace),
):
    """流式对话端点（SSE）— 支持 slash 命令 + LLM 流式输出"""

    async def event_stream():
        collected_response = []

        # 加载会话上下文
        context_messages = []
        if session_id:
            try:
                conn_ctx = get_db()
                cursor_ctx = conn_ctx.cursor()
                # 验证 session 存在
                cursor_ctx.execute(
                    "SELECT session_id FROM chat_sessions WHERE session_id = ?",
                    (session_id,)
                )
                if cursor_ctx.fetchone():
                    # 获取最近 10 条消息作为上下文
                    cursor_ctx.execute("""
                        SELECT role, content FROM chat_messages
                        WHERE session_id = ?
                        ORDER BY created_at DESC LIMIT 10
                    """, (session_id,))
                    rows = cursor_ctx.fetchall()
                    context_messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
                conn_ctx.close()
            except Exception:
                pass

        def save_messages():
            """保存用户消息和 AI 回复到会话"""
            if not session_id or not message:
                return
            try:
                import uuid as _uuid
                full_response = "".join(collected_response)
                conn_save = get_db()
                now = datetime.now().isoformat()
                src_count = len(vector_results) if vector_results else 0
                # 保存用户消息
                conn_save.execute("""
                    INSERT INTO chat_messages (message_id, session_id, role, content, created_at)
                    VALUES (?, ?, 'user', ?, ?)
                """, (f"msg_{_uuid.uuid4().hex[:12]}", session_id, message, now))
                # 保存 AI 回复
                if full_response:
                    conn_save.execute("""
                        INSERT INTO chat_messages (message_id, session_id, role, content, sources_count, created_at)
                        VALUES (?, ?, 'assistant', ?, ?, ?)
                    """, (f"msg_{_uuid.uuid4().hex[:12]}", session_id, full_response, src_count, now))
                # 更新 session
                conn_save.execute("""
                    UPDATE chat_sessions SET updated_at = ?, message_count = message_count + 2,
                    title = CASE WHEN message_count = 0 THEN ? ELSE title END
                    WHERE session_id = ?
                """, (now, message[:20], session_id))
                conn_save.commit()
                conn_save.close()
            except Exception:
                pass  # 不影响对话

        message = q.strip()

        # 按次扣费（非所有者对话时）
        user_id = user.get("user_id")
        conn_fee = get_db()
        cursor_fee = conn_fee.cursor()
        cursor_fee.execute("SELECT owner_id, price_per_chat FROM agents WHERE namespace = ?", (namespace,))
        agent_row = cursor_fee.fetchone()

        if agent_row and agent_row["owner_id"] != user_id:
            price = agent_row["price_per_chat"] or 0
            if price > 0:
                # 检查余额
                cursor_fee.execute("SELECT atp_balance FROM users WHERE user_id = ?", (user_id,))
                user_row = cursor_fee.fetchone()
                balance = user_row["atp_balance"] if user_row else 0
                if balance < price:
                    conn_fee.close()
                    yield f"data: {json.dumps({'type': 'error', 'message': f'ATP 余额不足，本次对话需要 {price} ATP，当前余额 {balance} ATP'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    return

                # 扣费
                import uuid as _uuid2
                cursor_fee.execute("UPDATE users SET atp_balance = atp_balance - ? WHERE user_id = ?", (price, user_id))
                cursor_fee.execute("UPDATE users SET atp_balance = atp_balance + ? WHERE user_id = ?", (price, agent_row["owner_id"]))
                tx_id = f"tx_{_uuid2.uuid4().hex[:12]}"
                cursor_fee.execute("""
                    INSERT INTO transactions (tx_id, from_user_id, to_user_id, agent_id, atp_amount, tx_type, description)
                    VALUES (?, ?, ?, (SELECT agent_id FROM agents WHERE namespace = ?), ?, 'chat_fee', ?)
                """, (tx_id, user_id, agent_row["owner_id"], namespace, price, f"对话费用 {price} ATP"))
                conn_fee.commit()
        conn_fee.close()

        # 提前加载 chat_config（后续多处复用）
        chat_config = _get_chat_config(namespace)

        # Slash 命令不流式，直接返回
        if message.startswith('/'):
            from cogmate_core.intent_handler import IntentHandler
            handler = IntentHandler(namespace=namespace)
            result = handler._handle_slash_command(message)
            collected_response.append(result)
            yield f"data: {json.dumps({'type': 'content', 'text': result})}\n\n"
            save_messages()
            sg = yield_suggestions()
            if sg:
                yield sg
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # 检索知识库（空知识库不应崩溃）
        try:
            retrieval_top_k = chat_config.get("retrieval_top_k", 5)
            retrieval_min_score = chat_config.get("retrieval_min_score", 0.5)
            cogmate = _get_cogmate(namespace)
            results = cogmate.query(query_text=message, top_k=retrieval_top_k, min_score=retrieval_min_score)
            vector_results = results.get("vector_results", [])
        except Exception as e:
            # Collection 不存在或知识库为空
            vector_results = []

        # 图谱推理扩展
        if chat_config.get("enable_graph_reasoning") and vector_results:
            try:
                fact_ids = [f.get("fact_id", "") for f in vector_results if f.get("fact_id")]
                graph_extra = _graph_reasoning(namespace, fact_ids)
                if graph_extra:
                    vector_results = vector_results + graph_extra
            except Exception:
                pass

        def yield_suggestions():
            """生成网络搜索建议存入事件（仅 owner 与自己的 Human Agent 对话时）"""
            if not web_suggestions:
                return ""
            # 只有 owner 才能存入知识
            if not user.get("_is_owner"):
                return ""
            return f"data: {json.dumps({'type': 'suggestions', 'items': web_suggestions}, ensure_ascii=False)}\n\n"

        # 网络搜索增强（开启后始终搜索，作为知识库的补充而非仅 fallback）
        web_suggestions = []
        if chat_config.get("enable_web_search"):
            try:
                web_results = _web_search_fallback(message)
                if web_results:
                    web_suggestions = [{"summary": w["summary"], "content_type": w["content_type"]} for w in web_results]
                    vector_results = vector_results + web_results
            except Exception:
                pass

        yield f"data: {json.dumps({'type': 'meta', 'sources_count': len(vector_results)})}\n\n"
        yield f"data: {json.dumps({'type': 'thinking', 'text': '正在思考...'})}\n\n"

        agent_info = _get_agent_info(namespace)
        is_character = agent_info.get("agent_type") == "character"
        llm_cfg = _get_agent_llm_config(namespace)

        if not vector_results and not is_character:
            # Human Agent: 严格模式，必须有知识库来源
            collected_response.append('📭 知识库中暂无相关内容。')
            yield f"data: {json.dumps({'type': 'content', 'text': '📭 知识库中暂无相关内容。'})}\n\n"
            save_messages()
            sg = yield_suggestions()
            if sg:
                yield sg
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        if not vector_results and is_character:
            # Character Agent: 知识库为空时用 LLM + persona 直接回答
            if not llm_cfg.get("api_key"):
                collected_response.append('⚠️ 请先在 Config 中配置 LLM API Key。')
                yield f"data: {json.dumps({'type': 'content', 'text': '⚠️ 请先在 Config 中配置 LLM API Key。'})}\n\n"
                save_messages()
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            try:
                import httpx as _httpx

                system_prompt = _build_dynamic_system_prompt(namespace, [], chat_config, context_messages)

                # 构建 LLM 请求
                provider = llm_cfg.get("provider", "")
                base_url = llm_cfg.get("endpoint", "")
                if not base_url:
                    provider_urls = {
                        "openai": "https://api.openai.com/v1",
                        "doubao": "https://ark.cn-beijing.volces.com/api/v3",
                        "deepseek": "https://api.deepseek.com/v1",
                        "moonshot": "https://api.moonshot.cn/v1",
                    }
                    base_url = provider_urls.get(provider, "https://api.openai.com/v1")

                async with _httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "POST",
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {llm_cfg['api_key']}", "Content-Type": "application/json"},
                        json={
                            "model": llm_cfg.get("model", "gpt-4o-mini"),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                *context_messages,
                                {"role": "user", "content": message},
                            ],
                            "stream": True,
                        },
                    ) as resp:

                        if resp.status_code != 200:
                            await resp.aread()
                            yield f"data: {json.dumps({'type': 'error', 'message': f'LLM 请求失败: {resp.status_code}'})}\n\n"
                            yield f"data: {json.dumps({'type': 'done'})}\n\n"
                            return

                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            chunk_data = line[6:]
                            if chunk_data.strip() == "[DONE]":
                                break
                            try:
                                import json as _json
                                chunk_obj = _json.loads(chunk_data)
                                delta = chunk_obj.get("choices", [{}])[0].get("delta", {})
                                text = delta.get("content", "")
                                if text:
                                    collected_response.append(text)
                                    yield f"data: {json.dumps({'type': 'content', 'text': text})}\n\n"
                            except Exception:
                                pass

            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

            save_messages()
            sg = yield_suggestions()
            if sg:
                yield sg
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # 有知识库结果：使用动态 prompt + LLM 流式回答
        if not llm_cfg.get("api_key"):
            # 无 LLM key，使用结构化输出
            from cogmate_core.llm_answer import _structured_answer
            try:
                result = _structured_answer(message, vector_results, "\n".join([f.get("summary","") for f in vector_results]), namespace)
                collected_response.append(result)
                yield f"data: {json.dumps({'type': 'content', 'text': result})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        else:
            try:
                import httpx as _httpx
                system_prompt = _build_dynamic_system_prompt(namespace, vector_results, chat_config, context_messages)
                
                provider = llm_cfg.get("provider", "")
                base_url = llm_cfg.get("endpoint", "")
                if not base_url:
                    provider_urls = {
                        "openai": "https://api.openai.com/v1",
                        "doubao": "https://ark.cn-beijing.volces.com/api/v3",
                        "deepseek": "https://api.deepseek.com/v1",
                        "moonshot": "https://api.moonshot.cn/v1",
                    }
                    base_url = provider_urls.get(provider, "https://api.openai.com/v1")
                
                max_tokens = chat_config.get("max_tokens", 2000)
                
                async with _httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "POST",
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {llm_cfg['api_key']}", "Content-Type": "application/json"},
                        json={
                            "model": llm_cfg.get("model", "gpt-4o-mini"),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                *context_messages,
                                {"role": "user", "content": message},
                            ],
                            "stream": True,
                            "max_tokens": max_tokens,
                        },
                    ) as resp:
                        if resp.status_code != 200:
                            await resp.aread()
                            yield f"data: {json.dumps({'type': 'error', 'message': f'LLM 请求失败: {resp.status_code}'})}\n\n"
                        else:
                            async for line in resp.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                chunk_data = line[6:]
                                if chunk_data.strip() == "[DONE]":
                                    break
                                try:
                                    chunk_obj = json.loads(chunk_data)
                                    delta = chunk_obj.get("choices", [{}])[0].get("delta", {})
                                    text = delta.get("content", "")
                                    if text:
                                        collected_response.append(text)
                                        yield f"data: {json.dumps({'type': 'content', 'text': text})}\n\n"
                                except Exception:
                                    pass
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        save_messages()
        sg = yield_suggestions()
        if sg:
            yield sg
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ==================== 统计 & 健康 ====================

@router.get("/stats")
async def get_stats(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """获取知识库统计概览"""
    stats = _safe_stats(namespace)

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
    _require_public_chat_only(user)
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

    # 语义搜索（空知识库不崩溃）
    results = _safe_query(namespace, request.question, top_k=request.max_sources * 2, min_score=0.5)

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

    # 语义搜索（空知识库不崩溃）
    results = _safe_query(namespace, q, top_k=10, min_score=0.5)
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
    stats = _safe_stats(namespace)

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
    _require_owner(user)
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


@router.put("/privacy/batch")
async def set_privacy_batch(
    namespace: str,
    request: PrivacyBatchRequest,
    user: dict = Depends(verify_namespace),
):
    """批量设置实体隐私状态"""
    _require_owner(user)
    from cogmate_core.privacy import (
        set_fact_private, set_abstract_private, get_privacy_status,
    )

    results = []
    for entity_id in request.entity_ids:
        status = get_privacy_status(entity_id)
        if not status:
            results.append({"entity_id": entity_id, "success": False, "error": "未找到"})
            continue

        try:
            if status["type"] == "fact":
                success = set_fact_private(entity_id, request.is_private)
                results.append({"entity_id": entity_id, "type": "fact", "success": success})
            else:
                success, _ = set_abstract_private(entity_id, request.is_private)
                results.append({"entity_id": entity_id, "type": "abstract", "success": success})
        except Exception as e:
            results.append({"entity_id": entity_id, "success": False, "error": str(e)})

    return {
        "is_private": request.is_private,
        "total": len(request.entity_ids),
        "results": results,
    }


@router.get("/privacy/list")
async def list_private_entities_endpoint(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """列出所有私有实体"""
    from cogmate_core.privacy import list_private_entities
    entities = list_private_entities()
    return {"entities": entities}


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


# ==================== Fact CRUD ====================

@router.get("/fact/{fact_id}")
async def get_fact(
    namespace: str,
    fact_id: str,
    user: dict = Depends(verify_namespace),
):
    """获取单条 fact 详情"""
    _require_public_chat_only(user)
    cogmate = _get_cogmate(namespace)
    fact = cogmate.get_fact(fact_id)
    if not fact:
        raise HTTPException(status_code=404, detail="Fact 未找到")
    return fact


@router.put("/fact/{fact_id}")
async def update_fact(
    namespace: str,
    fact_id: str,
    request: FactUpdateRequest,
    user: dict = Depends(verify_namespace),
):
    """更新 fact 的 summary（三库同步）"""
    _require_owner(user)
    cogmate = _get_cogmate(namespace)

    # 先确认 fact 存在
    existing = cogmate.get_fact(fact_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Fact 未找到")

    new_summary = request.summary
    content_type = request.content_type or existing.get("content_type", "事实")
    now = datetime.now().isoformat()

    try:
        from cogmate_core import get_sqlite, get_qdrant, get_neo4j, get_collection_name, CogmateAgent
        from qdrant_client.models import PointStruct

        # 1. SQLite
        conn = get_sqlite()
        conn.execute(
            "UPDATE facts SET summary=?, content_type=?, updated_at=? WHERE fact_id=?",
            (new_summary, content_type, now, fact_id),
        )
        conn.commit()
        conn.close()

        # 2. Neo4j
        driver = get_neo4j()
        with driver.session() as session:
            session.run(
                "MATCH (f:Fact {fact_id: $fid}) SET f.summary = $summary, f.content_type = $type",
                fid=fact_id, summary=new_summary, type=content_type,
            )

        # 3. Qdrant (re-embed + upsert)
        agent = CogmateAgent(namespace=namespace)
        vector = agent.embed(new_summary)
        client = get_qdrant()
        client.upsert(
            collection_name=get_collection_name(namespace),
            points=[
                PointStruct(
                    id=fact_id,
                    vector=vector,
                    payload={
                        "summary": new_summary,
                        "content_type": content_type,
                        "namespace": namespace,
                    },
                )
            ],
        )

        return {
            "success": True,
            "fact_id": fact_id,
            "summary": new_summary,
            "content_type": content_type,
            "updated_at": now,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


class StoreSuggestionRequest(BaseModel):
    summary: str
    content_type: str = "资讯"


@router.post("/store-suggestion")
async def store_suggestion(
    namespace: str,
    request: StoreSuggestionRequest,
    user: dict = Depends(verify_namespace),
):
    """存入建议的知识条目（带去重检查）"""
    _require_owner(user)
    
    # 去重检查：向量相似度 > 0.92 视为重复（高阈值避免误判）
    try:
        cogmate = _get_cogmate(namespace)
        existing = cogmate.query(query_text=request.summary, top_k=1, min_score=0.92)
        if existing.get("vector_results"):
            similar = existing["vector_results"][0]
            return {
                "success": False,
                "duplicate": True,
                "similar_fact": similar.get("summary", "")[:80],
                "message": "知识库中已有相似内容，跳过存入",
            }
    except Exception:
        pass
    
    # 存入三库
    try:
        cogmate = _get_cogmate(namespace)
        result = cogmate.store(
            request.summary,
            content_type=request.content_type,
            source_type="web_suggested",
        )
        return {
            "success": True,
            "fact_id": result.get("fact_id", ""),
            "message": "已存入知识库",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"存入失败: {str(e)}")


@router.delete("/fact/{fact_id}")
async def delete_fact(
    namespace: str,
    fact_id: str,
    user: dict = Depends(verify_namespace),
):
    """删除 fact（三库独立同步，确保每个库都执行）"""
    _require_owner(user)
    cogmate = _get_cogmate(namespace)

    existing = cogmate.get_fact(fact_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Fact 未找到")

    errors = []

    # 1. SQLite — 直接删除
    try:
        from cogmate_core import get_sqlite
        conn = get_sqlite()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
        cursor.execute("DELETE FROM associations WHERE from_fact_id = ? OR to_fact_id = ?", (fact_id, fact_id))
        conn.commit()
        conn.close()
    except Exception as e:
        errors.append(f"SQLite: {e}")

    # 2. Neo4j — DETACH DELETE
    try:
        from cogmate_core import get_neo4j
        driver = get_neo4j()
        with driver.session() as session:
            session.run("MATCH (f:Fact {fact_id: $fid}) DETACH DELETE f", fid=fact_id)
    except Exception as e:
        errors.append(f"Neo4j: {e}")

    # 3. Qdrant — 删除向量
    try:
        from cogmate_core import get_qdrant, get_collection_name
        from qdrant_client.models import PointIdsList
        client = get_qdrant()
        client.delete(
            collection_name=get_collection_name(namespace),
            points_selector=PointIdsList(points=[fact_id])
        )
    except Exception as e:
        errors.append(f"Qdrant: {e}")

    if errors:
        return {"success": True, "fact_id": fact_id, "message": "已删除（部分警告）", "warnings": errors}
    return {"success": True, "fact_id": fact_id, "message": "已删除"}


# ==================== 操作动作 ====================

@router.post("/action")
async def action(
    namespace: str,
    request: ActionRequest,
    user: dict = Depends(verify_namespace),
):
    """执行知识图谱操作"""
    _require_owner(user)
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
    _require_owner(user)
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


# ==================== Character 自动调研（异步后台执行）====================

import asyncio as _asyncio
import uuid as _uuid

# 内存中的任务状态（重启后丢失，调研任务幂等可重跑）
_research_tasks = {}  # task_id -> {status, progress, result, error, started_at, ...}


class ResearchRequest(BaseModel):
    reference_names: list  # 参考人物名列表, e.g. ["Elon Musk"]
    depth: str = "normal"  # normal | deep


@router.post("/research-character")
async def research_character_endpoint(
    namespace: str,
    request: ResearchRequest,
    user: dict = Depends(verify_namespace),
):
    """对 Character Agent 进行自动化调研（异步后台执行，立即返回 task_id）"""
    _require_owner(user)
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

    # 检查是否已有进行中的调研任务
    for tid, t in _research_tasks.items():
        if t.get("namespace") == namespace and t.get("status") == "running":
            return {"task_id": tid, "status": "running", "progress": t.get("progress", ""), "message": "已有调研任务进行中"}

    # 创建后台任务
    task_id = f"research_{_uuid.uuid4().hex[:8]}"
    _research_tasks[task_id] = {
        "status": "running",
        "namespace": namespace,
        "progress": "初始化...",
        "result": None,
        "error": None,
        "started_at": datetime.now().isoformat(),
    }

    async def _run_research():
        task = _research_tasks[task_id]
        try:
            # 在线程池中运行同步阻塞代码，不阻塞事件循环
            loop = _asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _do_research_sync,
                namespace, request.reference_names, request.depth,
                llm_config, agent_name, task_id)
            task["status"] = "completed"
            task["result"] = result
            task["progress"] = "完成"
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)
            task["progress"] = f"失败: {str(e)[:100]}"

    _asyncio.create_task(_run_research())

    return {
        "task_id": task_id,
        "status": "running",
        "progress": "初始化...",
        "message": "调研任务已启动，请通过 status 接口查询进度",
    }


def _do_research_sync(namespace, reference_names, depth, llm_config, agent_name, task_id):
    """同步执行调研（在线程池中运行）"""
    task = _research_tasks.get(task_id, {})

    from cogmate_core.character_research import (
        research_character, apply_persona_to_profile, store_initial_knowledge,
        discover_relations_with_llm, generate_abstracts_with_llm
    )

    # 1. 搜索 + 生成 Persona
    task["progress"] = "🔍 搜索资料 + 生成 Persona..."
    result = research_character(
        character_names=reference_names,
        depth=depth,
        llm_config=llm_config,
        agent_name=agent_name,
    )

    persona = result.get("persona")
    if not persona:
        raise Exception("未生成 Persona")

    # 2. 应用 Persona 到 Profile
    task["progress"] = "📝 应用 Persona..."
    apply_persona_to_profile(namespace, persona, reference_names)

    # 3. 存储知识到三库
    task["progress"] = "💾 存储知识到三库..."
    stored = store_initial_knowledge(namespace, persona)

    # 4. LLM 分析关联关系
    task["progress"] = "🔗 发现关联关系..."
    relations_created = discover_relations_with_llm(
        namespace, llm_config=llm_config, agent_name=agent_name)

    # 5. LLM 归纳总结
    task["progress"] = "🌳 生成抽象层..."
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


@router.get("/research-character/status")
async def research_status(
    namespace: str,
    task_id: str = Query(...),
    user: dict = Depends(verify_namespace),
):
    """查询调研任务进度"""
    task = _research_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在（可能服务已重启）")
    if task.get("namespace") != namespace:
        raise HTTPException(status_code=403, detail="无权查看")

    response = {
        "task_id": task_id,
        "status": task["status"],
        "progress": task.get("progress", ""),
        "started_at": task.get("started_at"),
    }
    if task["status"] == "completed":
        response["result"] = task["result"]
    if task["status"] == "failed":
        response["error"] = task["error"]

    # 清理已完成超过 10 分钟的任务
    now = datetime.now()
    to_clean = [tid for tid, t in _research_tasks.items()
                if t["status"] in ("completed", "failed") and
                (now - datetime.fromisoformat(t["started_at"])).seconds > 600]
    for tid in to_clean:
        del _research_tasks[tid]

    return response


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

    stats = _safe_stats(namespace)

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
