"""
Simulation Engine - Agent-Based Prediction System

核心模块: Simulation CRUD, 招募, 多轮采集, 聚合, 结算
"""
import os
import uuid
import json
import httpx
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from database import get_db
from crypto_utils import decrypt_api_key


# ==========================================
# LLM Integration (OpenClaw Gateway)
# ==========================================

LLM_ENABLED = os.environ.get("LLM_ENABLED", "false").lower() == "true"


def _load_openclaw_token() -> Optional[str]:
    """从 ~/.openclaw/openclaw.json 读取 gateway auth token"""
    try:
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(config_path, "r") as f:
            config = json.load(f)
        return config.get("gateway", {}).get("auth", {}).get("token")
    except Exception:
        return None


async def llm_call(system: str, user: str) -> Optional[str]:
    """
    通过 OpenClaw Gateway 调用 LLM

    URL: http://127.0.0.1:18789/v1/chat/completions
    Auth: Bearer token from OpenClaw config
    Model: 'openclaw'

    如果 LLM_ENABLED=false 或调用失败，返回 None（由调用方 fallback）
    """
    if not LLM_ENABLED:
        return None

    token = _load_openclaw_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                "http://127.0.0.1:18789/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openclaw",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.7,
                },
            )
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
    except Exception:
        pass

    return None


# ==========================================
# Helpers
# ==========================================

def _gen_id(prefix: str = "sim") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now().isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


# ==========================================
# Simulation CRUD
# ==========================================

def create_simulation(
    title: str,
    question: str,
    category: str,
    resolution_criteria: str,
    created_by: str,
    description: str = "",
    tags: List[str] = None,
    outcome_type: str = "binary",
    outcome_options: List[str] = None,
    resolution_source: str = None,
    total_rounds: int = 1,
    round_interval: str = None,
    min_agents: int = 3,
    max_agents: int = 50,
    stake_per_agent: int = 5,
    round_titles: List[str] = None,
) -> Dict[str, Any]:
    """创建 Simulation + 初始化轮次"""
    conn = get_db()
    cursor = conn.cursor()

    sim_id = _gen_id("sim")
    if outcome_options is None:
        outcome_options = ["yes", "no"]
    if tags is None:
        tags = []

    cursor.execute("""
        INSERT INTO simulations (
            simulation_id, title, description, question,
            category, tags,
            outcome_type, outcome_options, resolution_criteria, resolution_source,
            total_rounds, current_round, round_interval,
            min_agents, max_agents, stake_per_agent,
            status, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 'draft', ?, ?)
    """, (
        sim_id, title, description, question,
        category, json.dumps(tags),
        outcome_type, json.dumps(outcome_options), resolution_criteria, resolution_source,
        total_rounds, round_interval,
        min_agents, max_agents, stake_per_agent,
        created_by, _now()
    ))

    # 初始化轮次
    rounds = []
    for i in range(1, total_rounds + 1):
        round_id = _gen_id("rnd")
        round_title = ""
        if round_titles and i <= len(round_titles):
            round_title = round_titles[i - 1]
        cursor.execute("""
            INSERT INTO simulation_rounds (round_id, simulation_id, round_number, title, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (round_id, sim_id, i, round_title))
        rounds.append({"round_id": round_id, "round_number": i, "title": round_title})

    conn.commit()
    conn.close()

    return {
        "simulation_id": sim_id,
        "title": title,
        "total_rounds": total_rounds,
        "rounds": rounds,
        "status": "draft"
    }


def get_simulation(simulation_id: str) -> Optional[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM simulations WHERE simulation_id = ?", (simulation_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    result = _row_to_dict(row)
    # Parse JSON fields
    for field in ("tags", "outcome_options", "final_prediction"):
        if result.get(field) and isinstance(result[field], str):
            try:
                result[field] = json.loads(result[field])
            except:
                pass
    # 过滤敏感字段
    result.pop('llm_api_key_enc', None)
    return result


def list_simulations(
    status: str = None,
    category: str = None,
    limit: int = 50,
    offset: int = 0
) -> Dict:
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM simulations WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    sims = []
    for row in rows:
        sim = _row_to_dict(row)
        for field in ("tags", "outcome_options", "final_prediction"):
            if sim.get(field) and isinstance(sim[field], str):
                try:
                    sim[field] = json.loads(sim[field])
                except:
                    pass
        # 过滤敏感字段
        sim.pop('llm_api_key_enc', None)
        sims.append(sim)

    # Total count
    count_query = "SELECT COUNT(*) FROM simulations WHERE 1=1"
    count_params = []
    if status:
        count_query += " AND status = ?"
        count_params.append(status)
    if category:
        count_query += " AND category = ?"
        count_params.append(category)
    cursor.execute(count_query, count_params)
    total = cursor.fetchone()[0]

    conn.close()
    return {"simulations": sims, "total": total}


def update_simulation(simulation_id: str, **kwargs) -> bool:
    """更新 Simulation (仅 draft 状态可改核心字段)"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT status FROM simulations WHERE simulation_id = ?", (simulation_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    # JSON encode list fields
    for field in ("tags", "outcome_options"):
        if field in kwargs and isinstance(kwargs[field], list):
            kwargs[field] = json.dumps(kwargs[field])

    # Build SET clause
    allowed = {
        "title", "description", "question", "category", "tags",
        "outcome_type", "outcome_options", "resolution_criteria",
        "resolution_source", "total_rounds", "round_interval",
        "min_agents", "max_agents", "stake_per_agent",
        "status", "actual_outcome", "final_prediction",
        "current_round", "recruiting_at", "opens_at", "closes_at",
        "resolved_at", "settled_at"
    }
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)

    if not sets:
        conn.close()
        return False

    vals.append(simulation_id)
    cursor.execute(
        f"UPDATE simulations SET {', '.join(sets)} WHERE simulation_id = ?",
        vals
    )
    conn.commit()
    conn.close()
    return True


def delete_simulation(simulation_id: str) -> bool:
    """删除 Simulation (仅 draft)"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT status FROM simulations WHERE simulation_id = ?", (simulation_id,))
    row = cursor.fetchone()
    if not row or row["status"] != "draft":
        conn.close()
        return False

    cursor.execute("DELETE FROM round_reactions WHERE simulation_id = ?", (simulation_id,))
    cursor.execute("DELETE FROM simulation_rounds WHERE simulation_id = ?", (simulation_id,))
    cursor.execute("DELETE FROM simulation_participants WHERE simulation_id = ?", (simulation_id,))
    cursor.execute("DELETE FROM simulations WHERE simulation_id = ?", (simulation_id,))

    conn.commit()
    conn.close()
    return True


# ==========================================
# Rounds
# ==========================================

def get_rounds(simulation_id: str) -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM simulation_rounds
        WHERE simulation_id = ? ORDER BY round_number
    """, (simulation_id,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        r = _row_to_dict(row)
        if r.get("aggregated_result") and isinstance(r["aggregated_result"], str):
            try:
                r["aggregated_result"] = json.loads(r["aggregated_result"])
            except:
                pass
        result.append(r)
    return result


def get_round(simulation_id: str, round_number: int) -> Optional[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM simulation_rounds
        WHERE simulation_id = ? AND round_number = ?
    """, (simulation_id, round_number))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    r = _row_to_dict(row)
    if r.get("aggregated_result") and isinstance(r["aggregated_result"], str):
        try:
            r["aggregated_result"] = json.loads(r["aggregated_result"])
        except:
            pass
    return r


def update_round(round_id: str, **kwargs) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    allowed = {"title", "context", "status", "opens_at", "closes_at",
               "aggregated_result", "result_summary"}
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            if k == "aggregated_result" and isinstance(v, dict):
                v = json.dumps(v)
            vals.append(v)
    if not sets:
        conn.close()
        return False
    vals.append(round_id)
    cursor.execute(f"UPDATE simulation_rounds SET {', '.join(sets)} WHERE round_id = ?", vals)
    conn.commit()
    conn.close()
    return True


# ==========================================
# Participants
# ==========================================

def add_participant(
    simulation_id: str,
    agent_id: str,
    relevance_score: float = 0,
    influence_weight: float = 0.5,
    qualification_method: str = "auto",
    role: str = "",
    role_description: str = ""
) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO simulation_participants (
                simulation_id, agent_id,
                relevance_score, influence_weight, qualification_method,
                role, role_description, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'invited')
        """, (simulation_id, agent_id, relevance_score, influence_weight,
              qualification_method, role, role_description))
        conn.commit()
        return True
    except Exception as e:
        # Duplicate or FK error
        return False
    finally:
        conn.close()


def get_participants(simulation_id: str) -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sp.*, a.name as agent_name, a.agent_type, a.endpoint_url,
               a.namespace, a.description as agent_description, a.avatar_url
        FROM simulation_participants sp
        JOIN agents a ON sp.agent_id = a.agent_id
        WHERE sp.simulation_id = ?
    """, (simulation_id,))
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_dict(row) for row in rows]


def update_participant(simulation_id: str, agent_id: str, **kwargs) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    allowed = {"relevance_score", "influence_weight", "role", "role_description",
               "status", "stake_amount", "final_stance", "final_confidence",
               "was_correct", "reward_amount"}
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        conn.close()
        return False
    vals.extend([simulation_id, agent_id])
    cursor.execute(
        f"UPDATE simulation_participants SET {', '.join(sets)} "
        f"WHERE simulation_id = ? AND agent_id = ?",
        vals
    )
    conn.commit()
    conn.close()
    return True


# ==========================================
# Reactions
# ==========================================

def insert_reaction(
    round_id: str,
    simulation_id: str,
    agent_id: str,
    prompt: str,
    prompt_type: str = "predictive"
) -> str:
    conn = get_db()
    cursor = conn.cursor()
    reaction_id = _gen_id("rxn")
    cursor.execute("""
        INSERT INTO round_reactions (
            reaction_id, round_id, simulation_id, agent_id,
            prompt, prompt_type, status
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (reaction_id, round_id, simulation_id, agent_id, prompt, prompt_type))
    conn.commit()
    conn.close()
    return reaction_id


def update_reaction(reaction_id: str, **kwargs) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    allowed = {"response_text", "key_points", "sentiment",
               "stance", "confidence", "brief_reasoning",
               "knowledge_depth", "status", "collected_at",
               "owner_disputed", "owner_correction", "disputed_at"}
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            if k == "key_points" and isinstance(v, list):
                v = json.dumps(v)
            vals.append(v)
    if not sets:
        conn.close()
        return False
    vals.append(reaction_id)
    cursor.execute(f"UPDATE round_reactions SET {', '.join(sets)} WHERE reaction_id = ?", vals)
    conn.commit()
    conn.close()
    return True


def get_round_reactions(round_id: str) -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT rr.*, sp.role, sp.role_description,
               a.name as agent_name, a.agent_type
        FROM round_reactions rr
        JOIN simulation_participants sp
            ON rr.simulation_id = sp.simulation_id AND rr.agent_id = sp.agent_id
        JOIN agents a ON rr.agent_id = a.agent_id
        WHERE rr.round_id = ?
    """, (round_id,))
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        r = _row_to_dict(row)
        if r.get("key_points") and isinstance(r["key_points"], str):
            try:
                r["key_points"] = json.loads(r["key_points"])
            except:
                pass
        results.append(r)
    return results


def get_agent_reactions(agent_id: str) -> List[Dict]:
    """获取 Agent 的所有历史 React 记录"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT rr.*, s.title as sim_title, s.category,
               sr.round_number, sr.title as round_title
        FROM round_reactions rr
        JOIN simulations s ON rr.simulation_id = s.simulation_id
        JOIN simulation_rounds sr ON rr.round_id = sr.round_id
        WHERE rr.agent_id = ?
        ORDER BY rr.collected_at DESC
    """, (agent_id,))
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        r = _row_to_dict(row)
        if r.get("key_points") and isinstance(r["key_points"], str):
            try:
                r["key_points"] = json.loads(r["key_points"])
            except:
                pass
        results.append(r)
    return results


# ==========================================
# Agent Qualification
# ==========================================

async def evaluate_agent_qualification(
    agent: Dict,
    simulation: Dict
) -> Dict:
    """
    评估 Agent 参与 Simulation 的资质

    基于:
    1. 标签重叠
    2. Agent 描述 vs Simulation 问题 相关度（简单文本匹配）
    3. 历史评分

    返回: {qualified, relevance, influence, reason}
    """
    # 1. Tag overlap
    agent_tags = set()
    if agent.get("tags"):
        try:
            agent_tags = set(json.loads(agent["tags"]) if isinstance(agent["tags"], str) else agent["tags"])
        except:
            pass

    sim_tags = set()
    if simulation.get("tags"):
        try:
            sim_tags = set(simulation["tags"] if isinstance(simulation["tags"], list) else json.loads(simulation["tags"]))
        except:
            pass

    tag_overlap = len(agent_tags & sim_tags) / max(len(sim_tags), 1) if sim_tags else 0

    # 2. Simple text relevance (keyword overlap until we have embeddings)
    sim_words = set((simulation.get("question", "") + " " + simulation.get("description", "")).lower().split())
    agent_words = set((agent.get("description", "") or "").lower().split())
    word_overlap = len(sim_words & agent_words) / max(len(sim_words), 1) if sim_words else 0

    # Combine
    relevance = tag_overlap * 0.5 + min(word_overlap * 3, 1.0) * 0.5
    relevance = round(min(relevance, 1.0), 3)

    # 3. Influence from historical score
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agent_sim_scores WHERE agent_id = ?", (agent["agent_id"],))
    score_row = cursor.fetchone()
    conn.close()

    if score_row:
        score = _row_to_dict(score_row)
        category_acc_raw = score.get("accuracy_by_category", "{}")
        try:
            category_acc = json.loads(category_acc_raw) if isinstance(category_acc_raw, str) else category_acc_raw
        except:
            category_acc = {}
        cat_accuracy = category_acc.get(simulation["category"], 0.5)
        calibration = score.get("calibration_score", 0.5)
        experience = min(score.get("total_participated", 0) / 20, 1.0)
    else:
        cat_accuracy = 0.5
        calibration = 0.5
        experience = 0

    influence = round(cat_accuracy * 0.4 + calibration * 0.3 + experience * 0.1 + 0.2 * relevance, 3)

    qualified = relevance >= 0.05  # 宽松门槛，尽量多包含

    return {
        "qualified": qualified,
        "relevance": relevance,
        "influence": influence,
        "tag_overlap": round(tag_overlap, 3),
        "reason": f"相关度 {relevance:.0%}, 影响力 {influence:.0%}"
    }


async def recruit_agents(simulation_id: str) -> Dict:
    """
    自动招募: 评估所有活跃 Agent → 符合条件的加入参与者
    """
    sim = get_simulation(simulation_id)
    if not sim:
        return {"error": "simulation_not_found"}

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agents WHERE status = 'active'")
    agents = [_row_to_dict(row) for row in cursor.fetchall()]
    conn.close()

    recruited = []
    skipped = []

    # 如果 active agent 总数 < 10，全部招募
    all_recruit = len(agents) < 10

    for agent in agents:
        qual = await evaluate_agent_qualification(agent, sim)

        if all_recruit or qual["qualified"]:
            success = add_participant(
                simulation_id=simulation_id,
                agent_id=agent["agent_id"],
                relevance_score=qual["relevance"],
                influence_weight=qual["influence"],
                qualification_method="auto"
            )
            if success:
                recruited.append({
                    "agent_id": agent["agent_id"],
                    "name": agent["name"],
                    "agent_type": agent["agent_type"],
                    **qual
                })
        else:
            skipped.append({
                "agent_id": agent["agent_id"],
                "name": agent["name"],
                **qual
            })

    # 更新状态
    if recruited:
        update_simulation(simulation_id, status="recruiting", recruiting_at=_now())

    return {
        "recruited": recruited,
        "skipped": skipped,
        "total_recruited": len(recruited),
        "total_skipped": len(skipped)
    }


# ==========================================
# Smart Recruit (Simulation LLM)
# ==========================================

async def _call_sim_llm(base_url: str, api_key: str, model: str, system: str, user: str) -> Optional[str]:
    """通过 Simulation 自带的 LLM 配置调用（OpenAI 兼容格式）"""
    base = base_url.rstrip('/')
    # 已经是完整 chat/completions URL
    if '/chat/completions' in base:
        url = base
    # 火山引擎豆包: /api/v3 → /api/v3/chat/completions
    elif base.endswith('/api/v3') or base.endswith('/api/v2'):
        url = f"{base}/chat/completions"
    # 标准 OpenAI: /v1 → /v1/chat/completions
    elif base.endswith('/v1'):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"

    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    data = {
        'model': model,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': 2000,
        'temperature': 0.7
    }
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(url, headers=headers, json=data)
        res.raise_for_status()
        return res.json()['choices'][0]['message']['content']


async def smart_recruit(simulation_id: str) -> Dict:
    """
    智能招募流程:
    1. 获取所有 active Agent
    2. 如果平台 Agent < 10，直接全选；否则宽松语义匹配 top N
    3. 将候选 Agent 信息发给 Simulation 配置的 LLM
    4. LLM 返回：选中 Agent + 角色 + 首轮个性化问题
    5. 写入 participants + roles + round 1 prompts
    6. 更新 simulation status -> recruiting
    Fallback: 如果没配 LLM 或调用失败，退回 recruit_agents() 逻辑
    """
    sim = get_simulation(simulation_id)
    if not sim:
        return {"error": "simulation_not_found"}

    # 读取 LLM 配置（需要从数据库读加密的 key）
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT llm_base_url, llm_api_key_enc, llm_model FROM simulations WHERE simulation_id = ?",
        (simulation_id,)
    )
    llm_row = cursor.fetchone()
    conn.close()

    llm_base_url = llm_row['llm_base_url'] if llm_row else None
    llm_api_key_enc = llm_row['llm_api_key_enc'] if llm_row else None
    llm_model = llm_row['llm_model'] if llm_row else None

    if not llm_base_url or not llm_api_key_enc or not llm_model:
        # 没有 LLM 配置，fallback 到普通招募
        return await recruit_agents(simulation_id)

    try:
        api_key = decrypt_api_key(llm_api_key_enc)
    except Exception:
        return await recruit_agents(simulation_id)

    # 1. 获取所有 active Agent
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agents WHERE status = 'active'")
    agents = [_row_to_dict(row) for row in cursor.fetchall()]
    conn.close()

    if not agents:
        return {"error": "no_active_agents", "total_recruited": 0}

    # 2. 候选筛选
    if len(agents) < 10:
        candidates = agents
    else:
        # 宽松语义匹配，取 top 20
        scored = []
        for agent in agents:
            qual = await evaluate_agent_qualification(agent, sim)
            scored.append((agent, qual))
        scored.sort(key=lambda x: x[1]["relevance"], reverse=True)
        candidates = [a for a, _ in scored[:20]]

    # 3. 构建 LLM Prompt
    agents_info = "\n".join([
        f"- agent_id={a['agent_id']}, name={a['name']}, type={a['agent_type']}, "
        f"description={a.get('description', '') or ''}, tags={a.get('tags', '[]')}"
        for a in candidates
    ])

    options = sim.get("outcome_options", ["yes", "no"])
    if isinstance(options, str):
        options = json.loads(options)

    system_prompt = """你是 CogNexus 模拟推演系统的策划师。
你需要从候选 Agent 列表中选择合适的参与者，为每个参与者分配角色，并为第一轮设计个性化的问题。

输出格式（严格 JSON）：
{
  "selected_agents": [
    {
      "agent_id": "...",
      "role": "角色名",
      "role_description": "一句话描述",
      "round_1_prompt": "针对该角色的第一轮问题",
      "prompt_type": "narrative 或 predictive"
    }
  ],
  "strategy_summary": "一段话描述模拟策略"
}"""

    user_prompt = f"""Simulation 信息:
- 标题: {sim['title']}
- 核心问题: {sim['question']}
- 分类: {sim['category']}
- 描述: {sim.get('description', '')}
- 结果选项: {json.dumps(options, ensure_ascii=False)}
- 判定标准: {sim.get('resolution_criteria', '')}
- 总轮次: {sim.get('total_rounds', 1)}

候选 Agent 列表:
{agents_info}

请从中选择最合适的参与者，分配角色，并为第一轮设计个性化问题。"""

    try:
        llm_result = await _call_sim_llm(llm_base_url, api_key, llm_model, system_prompt, user_prompt)

        # 解析 JSON（处理可能的 markdown 代码块包裹）
        result_text = llm_result.strip()
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1] if '\n' in result_text else result_text[3:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            result_text = result_text.strip()

        parsed = json.loads(result_text)
        selected = parsed.get("selected_agents", [])
        strategy = parsed.get("strategy_summary", "")

        if not selected:
            return await recruit_agents(simulation_id)

        # 4. 为每个 selected agent 添加参与者 + 设置角色
        recruited = []
        round_1_prompts = []

        for sa in selected:
            agent_id = sa.get("agent_id", "")
            # 验证 agent_id 存在于候选列表
            if not any(a["agent_id"] == agent_id for a in candidates):
                continue

            qual = await evaluate_agent_qualification(
                next(a for a in candidates if a["agent_id"] == agent_id), sim
            )

            success = add_participant(
                simulation_id=simulation_id,
                agent_id=agent_id,
                relevance_score=qual["relevance"],
                influence_weight=qual["influence"],
                qualification_method="smart"
            )

            if success:
                update_participant(
                    simulation_id, agent_id,
                    role=sa.get("role", ""),
                    role_description=sa.get("role_description", "")
                )
                recruited.append({
                    "agent_id": agent_id,
                    "name": next((a["name"] for a in candidates if a["agent_id"] == agent_id), ""),
                    "role": sa.get("role", ""),
                    "role_description": sa.get("role_description", ""),
                    **qual
                })
                round_1_prompts.append({
                    "agent_id": agent_id,
                    "prompt_type": sa.get("prompt_type", "predictive"),
                    "prompt": sa.get("round_1_prompt", f"关于「{sim['question']}」，你的看法是什么？")
                })

        # 5. 存储 round 1 的 planned_prompts
        if round_1_prompts:
            rounds = get_rounds(simulation_id)
            if rounds:
                round_1 = rounds[0]
                conn = get_db()
                conn.execute(
                    "UPDATE simulation_rounds SET planned_prompts = ? WHERE round_id = ?",
                    (json.dumps(round_1_prompts, ensure_ascii=False), round_1["round_id"])
                )
                conn.commit()
                conn.close()

        # 6. 更新状态
        if recruited:
            update_simulation(simulation_id, status="recruiting", recruiting_at=_now())

        return {
            "recruited": recruited,
            "total_recruited": len(recruited),
            "strategy_summary": strategy,
            "method": "smart"
        }

    except Exception as e:
        # LLM 调用失败，fallback
        return await recruit_agents(simulation_id)


# ==========================================
# Role Assignment (LLM)
# ==========================================

async def assign_roles(
    simulation_id: str,
    llm_call=None
) -> List[Dict]:
    """
    为参与者分配角色
    如果提供 llm_call 函数则用 LLM 分配，否则用 Agent 自身信息
    """
    sim = get_simulation(simulation_id)
    participants = get_participants(simulation_id)

    if not participants:
        return []

    if llm_call:
        # LLM 批量分配角色
        agents_desc = "\n".join([
            f"- agent_id={p['agent_id']}, name={p['agent_name']}, "
            f"type={p['agent_type']}, description={p.get('agent_description', '')}"
            for p in participants
        ])

        prompt = f"""模拟主题: {sim['title']}
问题: {sim['question']}
类别: {sim['category']}

参与的 Agent:
{agents_desc}

为每个 Agent 分配一个角色（role）和角色描述（role_description）。
角色应该符合该 Agent 的特性，并且在模拟中有明确的视角。

JSON 输出:
[
  {{"agent_id": "...", "role": "角色名", "role_description": "一句话描述该角色在此模拟中的视角"}}
]"""

        try:
            result = await llm_call(
                system="你是模拟推演系统的角色分配器。为每个参与者分配一个合理的角色。",
                user=prompt
            )
            assignments = json.loads(result) if isinstance(result, str) else result
            for a in assignments:
                update_participant(
                    simulation_id, a["agent_id"],
                    role=a.get("role", ""),
                    role_description=a.get("role_description", "")
                )
            return assignments
        except Exception as e:
            pass  # Fall through to default

    # 默认: 用 Agent 自身信息
    results = []
    for p in participants:
        role = p["agent_name"]
        role_desc = p.get("agent_description", "") or f"{p['agent_type']} agent"
        update_participant(simulation_id, p["agent_id"],
                          role=role, role_description=role_desc)
        results.append({
            "agent_id": p["agent_id"],
            "role": role,
            "role_description": role_desc
        })

    return results


# ==========================================
# Prompt Generation (LLM)
# ==========================================

async def generate_round_prompts(
    simulation_id: str,
    round_number: int,
    llm_call=None
) -> List[Dict]:
    """
    为某轮的每个 Agent 生成角色化问题

    返回: [{agent_id, prompt_type, prompt}, ...]
    """
    sim = get_simulation(simulation_id)
    rnd = get_round(simulation_id, round_number)
    participants = get_participants(simulation_id)

    if not sim or not rnd or not participants:
        return []

    # 先检查 round 的 planned_prompts 字段（智能招募时 LLM 规划的）
    if rnd.get('planned_prompts'):
        try:
            planned = json.loads(rnd['planned_prompts']) if isinstance(rnd['planned_prompts'], str) else rnd['planned_prompts']
            if isinstance(planned, list) and len(planned) > 0:
                return planned
        except Exception:
            pass

    if llm_call:
        agents_info = "\n".join([
            f"- agent_id={p['agent_id']}, role={p.get('role', p['agent_name'])}, "
            f"type={p['agent_type']}, description={p.get('role_description', '')}"
            for p in participants
        ])

        options = sim.get("outcome_options", ["yes", "no"])
        if isinstance(options, str):
            options = json.loads(options)

        prompt = f"""模拟: {sim['title']}
预测问题: {sim['question']}
可选结果: {json.dumps(options)}
当前轮次: 第 {round_number} 轮 - {rnd.get('title', '')}

前序发展:
{rnd.get('context', '（首轮，无前序）')}

角色:
{agents_info}

为每个角色生成一个针对性问题。

问题分两类:
1. narrative — 适用于当事人、决策者、利益相关方（推动剧情）
2. predictive — 适用于分析师、专家、观察者（做出预测）

判断原则:
- 角色本身是事件的参与者/当事人 → narrative
- 角色是旁观者/分析者/预测者 → predictive
- 不确定时偏向 predictive

JSON 输出:
[
  {{"agent_id": "...", "prompt_type": "narrative|predictive", "prompt": "..."}}
]"""

        try:
            result = await llm_call(
                system="你是模拟推演系统的主持人。为每个参与角色生成一个针对性问题。",
                user=prompt
            )
            prompts = json.loads(result) if isinstance(result, str) else result
            return prompts
        except Exception as e:
            pass  # Fall through to default

    # 默认: 所有人用相同的 predictive 问题
    return [
        {
            "agent_id": p["agent_id"],
            "prompt_type": "predictive",
            "prompt": f"关于「{sim['question']}」，基于你的知识和判断，你的预测是什么？"
        }
        for p in participants
    ]


# ==========================================
# Reaction Collection
# ==========================================

async def collect_single_reaction(
    agent: Dict,
    prompt_data: Dict,
    simulation: Dict,
    rnd: Dict,
    timeout: int = 120,
    llm_fallback: bool = False
) -> Dict:
    """
    向单个 Agent 的 Cogmate 发起 React 请求
    如果 Cogmate 不可用且 Simulation 配了 LLM，则用 LLM 直接生成回应
    """
    endpoint = agent.get("endpoint_url", "").rstrip("/")
    ns = agent.get("namespace", "default")
    agent_id = agent["agent_id"]
    prompt_type = prompt_data.get("prompt_type", "predictive")
    prompt = prompt_data["prompt"]

    outcome_options = simulation.get("outcome_options", ["yes", "no"])
    if isinstance(outcome_options, str):
        outcome_options = json.loads(outcome_options)

    # === 尝试 Cogmate 端点 ===
    cogmate_error = None
    if endpoint:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT token_value FROM agent_tokens WHERE agent_id = ? AND validated = 1 LIMIT 1", (agent_id,))
        token_row = cursor.fetchone()
        conn.close()

        if token_row:
            payload = {
                "simulation_id": simulation["simulation_id"],
                "round_id": rnd["round_id"],
                "prompt": prompt,
                "prompt_type": prompt_type,
                "description": simulation.get("description", ""),
                "outcome_options": outcome_options,
                "previous_context": rnd.get("context", "")
            }
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post(
                        f"{endpoint}/api/simulation/react",
                        params={"token": token_row["token_value"], "ns": ns},
                        json=payload
                    )
                    if res.status_code == 200:
                        data = res.json()
                        data["agent_id"] = agent_id
                        data["status"] = "collected"
                        data["source"] = "cogmate"
                        return data
                    else:
                        cogmate_error = f"http_{res.status_code}"
            except Exception as e:
                cogmate_error = str(e)[:200]
        else:
            cogmate_error = "no_token"
    else:
        cogmate_error = "no_endpoint"

    # === Cogmate 失败 → LLM Fallback ===
    if not llm_fallback:
        return {"agent_id": agent_id, "status": "failed", "error": cogmate_error}

    # 读取 Simulation 的 LLM 配置
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT llm_base_url, llm_api_key_enc, llm_model FROM simulations WHERE simulation_id = ?",
                   (simulation["simulation_id"],))
    llm_row = cursor.fetchone()
    conn.close()

    if not llm_row or not llm_row["llm_api_key_enc"] or not llm_row["llm_base_url"]:
        return {"agent_id": agent_id, "status": "failed", "error": f"cogmate:{cogmate_error}, no_llm_fallback"}

    try:
        from crypto_utils import decrypt_api_key
        api_key = decrypt_api_key(llm_row["llm_api_key_enc"])
        base_url = llm_row["llm_base_url"]
        model = llm_row["llm_model"]

        role_name = agent.get("role", agent.get("agent_name", "Agent"))
        role_desc = agent.get("role_description", agent.get("agent_description", ""))
        agent_type = agent.get("agent_type", "human")

        system = f"你是「{role_name}」，{role_desc}。请以这个角色的身份回答。"

        if prompt_type == "predictive":
            options_str = " / ".join(outcome_options)
            user_msg = f"""{prompt}

请先简要分析，然后给出预测：

---PREDICTION---
{{"stance": "<{options_str}>", "confidence": <0.0-1.0>, "brief_reasoning": "<一句话理由>"}}"""
        else:
            user_msg = prompt

        llm_response = await _call_sim_llm(base_url, api_key, model, system, user_msg)

        if not llm_response:
            return {"agent_id": agent_id, "status": "failed", "error": f"cogmate:{cogmate_error}, llm_no_response"}

        result = {
            "agent_id": agent_id,
            "status": "collected",
            "source": "llm_fallback",
            "response_text": llm_response,
            "knowledge_depth": 0,
        }

        if prompt_type == "predictive":
            # 解析 prediction
            import re
            json_match = re.search(r'\{[^{}]*"stance"[^{}]*\}', llm_response, re.DOTALL)
            if json_match:
                try:
                    pred = json.loads(json_match.group())
                    result["stance"] = pred.get("stance", outcome_options[0])
                    result["confidence"] = max(0.0, min(1.0, float(pred.get("confidence", 0.5))))
                    result["brief_reasoning"] = pred.get("brief_reasoning", "")
                    result["response_text"] = llm_response.split("---PREDICTION---")[0].strip() if "---PREDICTION---" in llm_response else llm_response
                except (json.JSONDecodeError, ValueError):
                    result["stance"] = outcome_options[0]
                    result["confidence"] = 0.3
                    result["brief_reasoning"] = "LLM fallback, 无法解析预测"
            else:
                result["stance"] = outcome_options[0]
                result["confidence"] = 0.3
                result["brief_reasoning"] = "LLM fallback, 无法解析预测"
        else:
            # narrative
            result["key_points"] = []
            result["sentiment"] = "neutral"

        return result

    except Exception as e:
        return {"agent_id": agent_id, "status": "failed", "error": f"cogmate:{cogmate_error}, llm_fallback:{str(e)[:100]}"}


async def run_round(
    simulation_id: str,
    round_number: int,
    llm_call=None,
    timeout: int = 120
) -> Dict:
    """
    执行一轮完整采集流程

    1. 生成角色化问题
    2. 向所有 Agent 发起采集
    3. 存储反应
    4. 聚合结果
    5. 生成摘要

    返回: {round_id, reactions, aggregated, summary}
    """
    sim = get_simulation(simulation_id)
    rnd = get_round(simulation_id, round_number)
    participants = get_participants(simulation_id)

    if not sim or not rnd:
        return {"error": "not_found"}
    if not participants:
        return {"error": "no_participants"}

    # 1. 生成角色化问题
    prompts = await generate_round_prompts(simulation_id, round_number, llm_call)

    # 建立 prompt 查找表
    prompt_by_agent = {p["agent_id"]: p for p in prompts}

    # 2. 为每个 Agent 创建 pending reaction + 并发采集
    collection_tasks = []
    for p in participants:
        agent_id = p["agent_id"]
        prompt_data = prompt_by_agent.get(agent_id, {
            "agent_id": agent_id,
            "prompt_type": "predictive",
            "prompt": f"关于「{sim['question']}」，你的预测是什么？"
        })

        # 创建 pending reaction
        reaction_id = insert_reaction(
            round_id=rnd["round_id"],
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=prompt_data["prompt"],
            prompt_type=prompt_data.get("prompt_type", "predictive")
        )

        collection_tasks.append({
            "reaction_id": reaction_id,
            "agent": p,
            "prompt_data": prompt_data
        })

    # 3. 并发采集
    update_round(rnd["round_id"], status="active", opens_at=_now())
    update_simulation(simulation_id, status="active", current_round=round_number)

    async def _collect_one(task):
        result = await collect_single_reaction(
            agent=task["agent"],
            prompt_data=task["prompt_data"],
            simulation=sim,
            rnd=rnd,
            timeout=timeout
        )
        # 更新 reaction
        if result.get("status") == "collected":
            update_kwargs = {
                "status": "collected",
                "collected_at": _now(),
                "response_text": result.get("response_text", ""),
                "knowledge_depth": result.get("knowledge_depth", 0),
            }
            if task["prompt_data"].get("prompt_type") == "narrative":
                update_kwargs["key_points"] = result.get("key_points", [])
                update_kwargs["sentiment"] = result.get("sentiment", "")
            else:
                update_kwargs["stance"] = result.get("stance", "")
                update_kwargs["confidence"] = result.get("confidence", 0)
                update_kwargs["brief_reasoning"] = result.get("brief_reasoning", "")
            update_reaction(task["reaction_id"], **update_kwargs)
        else:
            update_reaction(task["reaction_id"], status="failed")

        result["reaction_id"] = task["reaction_id"]
        return result

    results = await asyncio.gather(
        *[_collect_one(t) for t in collection_tasks],
        return_exceptions=True
    )

    # 处理异常
    clean_results = []
    for r in results:
        if isinstance(r, Exception):
            clean_results.append({"status": "failed", "error": str(r)})
        else:
            clean_results.append(r)

    # 4. 统计成功/失败
    collected_count = sum(1 for r in clean_results if r.get("status") == "collected")
    failed_count = sum(1 for r in clean_results if r.get("status") == "failed")
    all_collected = failed_count == 0

    # 5. 聚合（即使部分失败也聚合已有的）
    aggregated = aggregate_round(rnd["round_id"])

    # 6. 生成摘要
    summary = _generate_simple_summary(sim, rnd, clean_results, aggregated)

    # 7. 更新轮次状态：有失败的保持 active（允许重试），全部成功才 close
    if all_collected:
        update_round(
            rnd["round_id"],
            status="closed",
            closes_at=_now(),
            aggregated_result=aggregated,
            result_summary=summary
        )
    else:
        # 部分失败：保持 active，存储已有结果，等用户重试或手动 close
        update_round(
            rnd["round_id"],
            status="active",
            aggregated_result=aggregated,
            result_summary=f"{summary}\n⚠️ {failed_count} 个 Agent 采集失败，可重试"
        )

    # 8. 只在轮次 closed 时处理后续
    if all_collected:
        if round_number == sim["total_rounds"]:
            _update_final_stances(simulation_id, rnd["round_id"])
            update_simulation(
                simulation_id,
                status="closed",
                closes_at=_now(),
                final_prediction=json.dumps(aggregated.get("prediction", {}))
            )

        if round_number < sim["total_rounds"]:
            all_summaries = _get_all_summaries(simulation_id)
            next_rnd = get_round(simulation_id, round_number + 1)
            if next_rnd:
                update_round(next_rnd["round_id"], context="\n\n".join(all_summaries))

    return {
        "round_id": rnd["round_id"],
        "round_number": round_number,
        "reactions": clean_results,
        "aggregated": aggregated,
        "summary": summary,
        "collected": sum(1 for r in clean_results if r.get("status") == "collected"),
        "failed": sum(1 for r in clean_results if r.get("status") == "failed"),
    }


def _update_final_stances(simulation_id: str, last_round_id: str):
    """用最后一轮的 predictive 反应更新 participants 的 final_stance"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT agent_id, stance, confidence FROM round_reactions
        WHERE round_id = ? AND prompt_type = 'predictive' AND status = 'collected'
    """, (last_round_id,))
    for row in cursor.fetchall():
        cursor.execute("""
            UPDATE simulation_participants
            SET final_stance = ?, final_confidence = ?
            WHERE simulation_id = ? AND agent_id = ?
        """, (row["stance"], row["confidence"], simulation_id, row["agent_id"]))
    conn.commit()
    conn.close()


def _get_all_summaries(simulation_id: str) -> List[str]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT round_number, result_summary FROM simulation_rounds
        WHERE simulation_id = ? AND result_summary IS NOT NULL
        ORDER BY round_number
    """, (simulation_id,))
    summaries = [f"第{row['round_number']}轮: {row['result_summary']}" for row in cursor.fetchall()]
    conn.close()
    return summaries


def _generate_simple_summary(
    sim: Dict, rnd: Dict,
    results: List[Dict], aggregated: Dict
) -> str:
    """生成简单的轮次摘要（不依赖 LLM）"""
    parts = []
    parts.append(f"第{rnd['round_number']}轮 - {rnd.get('title', '')}")

    # Narratives
    narratives = [r for r in results
                  if r.get("status") == "collected" and r.get("prompt_type") == "narrative"]
    for n in narratives:
        name = n.get("agent_name", n.get("agent_id", "?"))
        text = (n.get("response_text", "") or "")[:200]
        parts.append(f"【{name}】{text}")

    # Prediction summary
    pred = aggregated.get("prediction", {})
    if pred:
        pred_str = ", ".join([f"{k}: {v:.0%}" for k, v in pred.items()])
        parts.append(f"预测汇总: {pred_str} (参与 {aggregated.get('total_agents', 0)} 位)")

    return "\n".join(parts)


# ==========================================
# Aggregation
# ==========================================

def aggregate_round(round_id: str) -> Dict:
    """聚合某轮的 predictive 反应"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT rr.agent_id, rr.stance, rr.confidence,
               sp.relevance_score, sp.influence_weight
        FROM round_reactions rr
        JOIN simulation_participants sp
            ON rr.simulation_id = sp.simulation_id AND rr.agent_id = sp.agent_id
        WHERE rr.round_id = ?
            AND rr.prompt_type = 'predictive'
            AND rr.status = 'collected'
            AND rr.stance IS NOT NULL
    """, (round_id,))

    reactions = cursor.fetchall()
    conn.close()

    if not reactions:
        return {"prediction": {}, "total_agents": 0, "total_weight": 0}

    outcome_scores = {}
    total_weight = 0
    breakdown = []

    for r in reactions:
        rel = r["relevance_score"] or 0.5
        inf = r["influence_weight"] or 0.5
        conf = r["confidence"] or 0.5
        weight = rel * inf * conf

        stance = r["stance"]
        outcome_scores[stance] = outcome_scores.get(stance, 0) + weight
        total_weight += weight

        breakdown.append({
            "agent_id": r["agent_id"],
            "stance": stance,
            "confidence": conf,
            "weight": round(weight, 4)
        })

    prediction = {}
    if total_weight > 0:
        prediction = {
            outcome: round(score / total_weight, 4)
            for outcome, score in outcome_scores.items()
        }

    top_outcome = max(prediction, key=prediction.get) if prediction else None

    return {
        "prediction": prediction,
        "top_outcome": top_outcome,
        "top_probability": prediction.get(top_outcome, 0) if top_outcome else 0,
        "total_agents": len(reactions),
        "total_weight": round(total_weight, 4),
        "agent_breakdown": breakdown
    }


# ==========================================
# Settlement
# ==========================================

def settle_simulation(simulation_id: str, actual_outcome: str) -> Dict:
    """
    判定结果 + ATP 结算

    reward = stake × (2 × confidence - 1) × direction
    direction = +1 (正确) / -1 (错误)
    """
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM simulations WHERE simulation_id = ?", (simulation_id,))
    sim = _row_to_dict(cursor.fetchone())
    if not sim:
        conn.close()
        return {"error": "not_found"}

    stake = sim["stake_per_agent"]

    # 更新 actual_outcome
    cursor.execute("""
        UPDATE simulations SET actual_outcome = ?, resolved_at = ?, status = 'resolved'
        WHERE simulation_id = ?
    """, (actual_outcome, _now(), simulation_id))

    # 获取所有有 final_stance 的参与者
    cursor.execute("""
        SELECT sp.*, a.owner_id
        FROM simulation_participants sp
        JOIN agents a ON sp.agent_id = a.agent_id
        WHERE sp.simulation_id = ? AND sp.final_stance IS NOT NULL
    """, (simulation_id,))

    participants = cursor.fetchall()
    total_rewards = 0
    total_correct = 0
    details = []

    for p in participants:
        correct = p["final_stance"] == actual_outcome
        conf = p["final_confidence"] or 0.5
        direction = 1 if correct else -1

        reward = int(stake * (2 * conf - 1) * direction)

        if correct:
            total_correct += 1

        # 更新参与者
        cursor.execute("""
            UPDATE simulation_participants
            SET was_correct = ?, reward_amount = ?
            WHERE simulation_id = ? AND agent_id = ?
        """, (1 if correct else 0, reward, simulation_id, p["agent_id"]))

        # 更新 owner ATP
        cursor.execute("""
            UPDATE users SET atp_balance = atp_balance + ?
            WHERE user_id = ?
        """, (reward, p["owner_id"]))

        # 记录交易
        tx_type = "reward" if reward >= 0 else "purchase"  # reuse existing types
        tx_id = _gen_id("tx")
        cursor.execute("""
            INSERT INTO transactions (tx_id, to_user_id, agent_id, atp_amount, tx_type, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (tx_id, p["owner_id"], p["agent_id"], reward, tx_type,
              f"Simulation 结算: {sim['title'][:50]}"))

        # 更新 agent_sim_scores
        _update_agent_score(cursor, p["agent_id"], correct, conf, sim["category"])

        total_rewards += reward
        details.append({
            "agent_id": p["agent_id"],
            "owner_id": p["owner_id"],
            "stance": p["final_stance"],
            "confidence": conf,
            "correct": correct,
            "reward": reward
        })

    # 创建结算记录
    settlement_id = _gen_id("stl")
    cursor.execute("""
        INSERT INTO simulation_settlements (
            settlement_id, simulation_id, total_agents, total_correct,
            total_stake_collected, total_rewards_distributed, settlement_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (settlement_id, simulation_id, len(details), total_correct,
          stake * len(details), total_rewards, json.dumps(details)))

    # 更新 simulation 状态
    cursor.execute("""
        UPDATE simulations SET status = 'settled', settled_at = ?
        WHERE simulation_id = ?
    """, (_now(), simulation_id))

    conn.commit()
    conn.close()

    return {
        "settlement_id": settlement_id,
        "actual_outcome": actual_outcome,
        "total_agents": len(details),
        "total_correct": total_correct,
        "total_rewards": total_rewards,
        "details": details
    }


def _update_agent_score(cursor, agent_id: str, correct: bool, confidence: float, category: str):
    """更新 Agent 的 Simulation 历史评分"""
    cursor.execute("SELECT * FROM agent_sim_scores WHERE agent_id = ?", (agent_id,))
    row = cursor.fetchone()

    if row:
        score = _row_to_dict(row)
        total = score["total_participated"] + 1
        total_correct = score["total_correct"] + (1 if correct else 0)
        accuracy = total_correct / total

        # 更新分类准确率
        try:
            cat_acc = json.loads(score["accuracy_by_category"]) if isinstance(score["accuracy_by_category"], str) else score["accuracy_by_category"]
        except:
            cat_acc = {}

        cat_total_key = f"__{category}_total"
        cat_correct_key = f"__{category}_correct"
        cat_acc[cat_total_key] = cat_acc.get(cat_total_key, 0) + 1
        cat_acc[cat_correct_key] = cat_acc.get(cat_correct_key, 0) + (1 if correct else 0)
        cat_acc[category] = cat_acc[cat_correct_key] / cat_acc[cat_total_key]

        # 简单校准度更新 (EMA)
        expected = confidence
        actual = 1.0 if correct else 0.0
        old_cal = score["calibration_score"]
        new_cal = old_cal * 0.9 + (1.0 - abs(expected - actual)) * 0.1

        # 平均 confidence (EMA)
        old_avg_conf = score["avg_confidence"]
        new_avg_conf = old_avg_conf * 0.9 + confidence * 0.1

        cursor.execute("""
            UPDATE agent_sim_scores SET
                total_participated = ?, total_correct = ?, accuracy_rate = ?,
                accuracy_by_category = ?, calibration_score = ?, avg_confidence = ?,
                last_participated_at = ?, updated_at = ?
            WHERE agent_id = ?
        """, (total, total_correct, accuracy, json.dumps(cat_acc),
              round(new_cal, 4), round(new_avg_conf, 4), _now(), _now(), agent_id))
    else:
        cat_acc = {
            category: 1.0 if correct else 0.0,
            f"__{category}_total": 1,
            f"__{category}_correct": 1 if correct else 0
        }
        cursor.execute("""
            INSERT INTO agent_sim_scores (
                agent_id, total_participated, total_correct, accuracy_rate,
                accuracy_by_category, avg_confidence, calibration_score,
                last_participated_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, 0.5, ?, ?)
        """, (agent_id, 1 if correct else 0, 1.0 if correct else 0.0,
              json.dumps(cat_acc), confidence, _now(), _now()))


# ==========================================
# Dispute
# ==========================================

def dispute_reaction(
    reaction_id: str,
    user_id: str,
    correction_stance: str = None,
    correction_confidence: float = None,
    correction_text: str = None,
    reason: str = ""
) -> Dict:
    """Agent Owner 标记某次反应不准确"""
    conn = get_db()
    cursor = conn.cursor()

    # 验证
    cursor.execute("""
        SELECT rr.*, a.owner_id, sr.status as round_status, rr.prompt_type
        FROM round_reactions rr
        JOIN agents a ON rr.agent_id = a.agent_id
        JOIN simulation_rounds sr ON rr.round_id = sr.round_id
        WHERE rr.reaction_id = ?
    """, (reaction_id,))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"error": "not_found"}

    reaction = _row_to_dict(row)

    if reaction["owner_id"] != user_id:
        conn.close()
        return {"error": "forbidden", "message": "只能修正自己 Agent 的反应"}

    # 构建修正记录
    correction = {
        "original_stance": reaction.get("stance"),
        "original_confidence": reaction.get("confidence"),
        "original_text": (reaction.get("response_text") or "")[:500],
        "correction_stance": correction_stance,
        "correction_confidence": correction_confidence,
        "correction_text": correction_text,
        "reason": reason,
        "disputed_at": _now()
    }

    update_kwargs = {
        "owner_disputed": 1,
        "owner_correction": json.dumps(correction),
        "disputed_at": _now(),
    }

    # predictive: 修正 stance/confidence
    if reaction["prompt_type"] == "predictive":
        if correction_stance is not None:
            update_kwargs["stance"] = correction_stance
        if correction_confidence is not None:
            update_kwargs["confidence"] = correction_confidence

    # narrative: 修正 response_text
    if correction_text is not None:
        update_kwargs["response_text"] = correction_text

    update_reaction(reaction_id, **update_kwargs)

    conn.close()
    return {"success": True, "message": "已标记并修正"}


# ==========================================
# Leaderboard
# ==========================================

def get_leaderboard(limit: int = 20) -> List[Dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, a.name as agent_name, a.agent_type, a.avatar_url
        FROM agent_sim_scores s
        JOIN agents a ON s.agent_id = a.agent_id
        WHERE s.total_participated >= 1
        ORDER BY s.accuracy_rate DESC, s.calibration_score DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        r = _row_to_dict(row)
        if r.get("accuracy_by_category") and isinstance(r["accuracy_by_category"], str):
            try:
                raw = json.loads(r["accuracy_by_category"])
                # 过滤掉内部计数键
                r["accuracy_by_category"] = {k: v for k, v in raw.items() if not k.startswith("__")}
            except:
                pass
        results.append(r)
    return results
