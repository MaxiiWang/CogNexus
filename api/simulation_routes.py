"""
Simulation API Routes
"""
import json
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, Header
from pydantic import BaseModel

from simulation import (
    create_simulation, get_simulation, list_simulations,
    update_simulation, delete_simulation,
    get_rounds, get_round, update_round,
    get_participants, add_participant, update_participant,
    get_round_reactions, get_agent_reactions,
    recruit_agents, assign_roles, run_round,
    aggregate_round, settle_simulation,
    dispute_reaction, get_leaderboard,
    evaluate_agent_qualification,
    smart_recruit,
    llm_call, LLM_ENABLED
)
from auth import verify_token


router = APIRouter(prefix="/api/simulations", tags=["simulation"])


# ==========================================
# Auth dependency (standalone, no circular import)
# ==========================================

async def get_current_user(authorization: str = Header(None)):
    """获取当前用户"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ")[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    return payload


# ==========================================
# Models
# ==========================================

class SimulationCreate(BaseModel):
    title: str
    question: str
    category: str
    resolution_criteria: str
    description: str = ""
    tags: List[str] = []
    outcome_type: str = "binary"
    outcome_options: List[str] = ["yes", "no"]
    resolution_source: str = None
    total_rounds: int = 1
    round_interval: str = None
    min_agents: int = 3
    max_agents: int = 50
    stake_per_agent: int = 5
    round_titles: List[str] = []
    llm_base_url: str = None
    llm_api_key: str = None  # 明文传入，后端加密存储
    llm_model: str = None


class SimulationUpdate(BaseModel):
    title: str = None
    description: str = None
    question: str = None
    category: str = None
    tags: List[str] = None
    outcome_options: List[str] = None
    resolution_criteria: str = None
    resolution_source: str = None
    total_rounds: int = None
    round_interval: str = None
    min_agents: int = None
    max_agents: int = None
    stake_per_agent: int = None


class ParticipantUpdate(BaseModel):
    role: str = None
    role_description: str = None
    relevance_score: float = None
    influence_weight: float = None


class InviteAgent(BaseModel):
    agent_id: str
    role: str = ""
    role_description: str = ""


class ResolveRequest(BaseModel):
    actual_outcome: str


class DisputeRequest(BaseModel):
    correction_stance: str = None
    correction_confidence: float = None
    correction_text: str = None
    reason: str = ""


class RoundUpdate(BaseModel):
    title: str = None
    context: str = None


# ==========================================
# Simulation CRUD
# ==========================================

@router.post("")
async def api_create_simulation(
    data: SimulationCreate,
    user: dict = Depends(get_current_user)
):
    """创建 Simulation"""
    result = create_simulation(
        title=data.title,
        question=data.question,
        category=data.category,
        resolution_criteria=data.resolution_criteria,
        created_by=user["user_id"],
        description=data.description,
        tags=data.tags,
        outcome_type=data.outcome_type,
        outcome_options=data.outcome_options,
        resolution_source=data.resolution_source,
        total_rounds=data.total_rounds,
        round_interval=data.round_interval,
        min_agents=data.min_agents,
        max_agents=data.max_agents,
        stake_per_agent=data.stake_per_agent,
        round_titles=data.round_titles
    )

    # 如果有 LLM 配置则加密存储
    if data.llm_api_key:
        from crypto_utils import encrypt_api_key
        from database import get_db
        conn = get_db()
        conn.execute(
            'UPDATE simulations SET llm_base_url=?, llm_api_key_enc=?, llm_model=? WHERE simulation_id=?',
            (data.llm_base_url, encrypt_api_key(data.llm_api_key), data.llm_model, result['simulation_id'])
        )
        conn.commit()
        conn.close()

    return result


@router.get("")
async def api_list_simulations(
    status: str = Query(None),
    category: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """列出 Simulations"""
    return list_simulations(status=status, category=category, limit=limit, offset=offset)


@router.get("/{simulation_id}")
async def api_get_simulation(simulation_id: str):
    """获取 Simulation 详情"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")

    # 附加参与者和轮次信息
    sim["participants"] = get_participants(simulation_id)
    sim["rounds"] = get_rounds(simulation_id)

    return sim


@router.put("/{simulation_id}")
async def api_update_simulation(
    simulation_id: str,
    data: SimulationUpdate,
    user: dict = Depends(get_current_user)
):
    """更新 Simulation (仅 draft)"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权修改")
    if sim["status"] != "draft":
        raise HTTPException(400, "只能修改 draft 状态的 Simulation")

    updates = {k: v for k, v in data.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "无更新内容")

    update_simulation(simulation_id, **updates)
    return {"success": True}


@router.delete("/{simulation_id}")
async def api_delete_simulation(
    simulation_id: str,
    user: dict = Depends(get_current_user)
):
    """删除 Simulation (仅 draft)"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权删除")

    success = delete_simulation(simulation_id)
    if not success:
        raise HTTPException(400, "只能删除 draft 状态的 Simulation")
    return {"success": True}


# ==========================================
# Recruit & Participants
# ==========================================

@router.post("/{simulation_id}/recruit")
async def api_recruit(
    simulation_id: str,
    user: dict = Depends(get_current_user)
):
    """自动招募 Agent"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")
    if sim["status"] not in ("draft", "recruiting"):
        raise HTTPException(400, f"当前状态 {sim['status']} 不支持招募")

    result = await recruit_agents(simulation_id)
    return result


@router.post("/{simulation_id}/smart-recruit")
async def api_smart_recruit(
    simulation_id: str,
    user: dict = Depends(get_current_user)
):
    """智能招募 Agent（使用 Simulation 配置的 LLM）"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")
    if sim["status"] not in ("draft", "recruiting"):
        raise HTTPException(400, f"当前状态 {sim['status']} 不支持招募")

    result = await smart_recruit(simulation_id)

    if "error" in result and result["error"] != "simulation_not_found":
        raise HTTPException(400, result.get("error", "招募失败"))

    return result


class LLMConfigUpdate(BaseModel):
    llm_base_url: str = None
    llm_api_key: str = None
    llm_model: str = None


@router.put("/{simulation_id}/llm-config")
async def api_update_llm_config(
    simulation_id: str,
    data: LLMConfigUpdate,
    user: dict = Depends(get_current_user)
):
    """更新 Simulation 的 LLM 配置"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")
    if sim["status"] != "draft":
        raise HTTPException(400, "只能在 draft 状态修改 LLM 配置")

    from crypto_utils import encrypt_api_key
    from database import get_db

    conn = get_db()
    if data.llm_api_key:
        conn.execute(
            "UPDATE simulations SET llm_base_url=?, llm_api_key_enc=?, llm_model=? WHERE simulation_id=?",
            (data.llm_base_url, encrypt_api_key(data.llm_api_key), data.llm_model, simulation_id)
        )
    elif data.llm_base_url:
        conn.execute(
            "UPDATE simulations SET llm_base_url=?, llm_model=? WHERE simulation_id=?",
            (data.llm_base_url, data.llm_model, simulation_id)
        )
    conn.commit()
    conn.close()
    return {"success": True}


@router.post("/{simulation_id}/assign-roles")
async def api_assign_roles(
    simulation_id: str,
    user: dict = Depends(get_current_user)
):
    """为参与者分配角色 (默认用 Agent 自身信息)"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    result = await assign_roles(simulation_id, llm_call=llm_call if LLM_ENABLED else None)
    return {"assignments": result}


@router.get("/{simulation_id}/participants")
async def api_get_participants(simulation_id: str):
    """查看参与者列表"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    return {"participants": get_participants(simulation_id)}


@router.post("/{simulation_id}/invite")
async def api_invite_agent(
    simulation_id: str,
    data: InviteAgent,
    user: dict = Depends(get_current_user)
):
    """手动邀请 Agent"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    # 评估资质
    from database import get_db
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agents WHERE agent_id = ?", (data.agent_id,))
    agent = cursor.fetchone()
    conn.close()

    if not agent:
        raise HTTPException(404, "Agent not found")

    agent_dict = dict(agent)
    qual = await evaluate_agent_qualification(agent_dict, sim)

    success = add_participant(
        simulation_id=simulation_id,
        agent_id=data.agent_id,
        relevance_score=qual["relevance"],
        influence_weight=qual["influence"],
        qualification_method="manual",
        role=data.role,
        role_description=data.role_description
    )

    if not success:
        raise HTTPException(400, "添加失败（可能已存在）")

    return {"success": True, "qualification": qual}


@router.put("/{simulation_id}/participants/{agent_id}")
async def api_update_participant(
    simulation_id: str,
    agent_id: str,
    data: ParticipantUpdate,
    user: dict = Depends(get_current_user)
):
    """修改参与者角色/权重"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    updates = {k: v for k, v in data.dict().items() if v is not None}
    update_participant(simulation_id, agent_id, **updates)
    return {"success": True}


# ==========================================
# Rounds
# ==========================================

@router.get("/{simulation_id}/rounds")
async def api_get_rounds(simulation_id: str):
    """获取所有轮次"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    return {"rounds": get_rounds(simulation_id)}


@router.put("/{simulation_id}/rounds/{round_number}")
async def api_update_round(
    simulation_id: str,
    round_number: int,
    data: RoundUpdate,
    user: dict = Depends(get_current_user)
):
    """更新轮次信息"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    rnd = get_round(simulation_id, round_number)
    if not rnd:
        raise HTTPException(404, "Round not found")

    updates = {k: v for k, v in data.dict().items() if v is not None}
    if updates:
        update_round(rnd["round_id"], **updates)
    return {"success": True}


@router.post("/{simulation_id}/rounds/{round_number}/run")
async def api_run_round(
    simulation_id: str,
    round_number: int,
    user: dict = Depends(get_current_user)
):
    """执行某轮采集"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    rnd = get_round(simulation_id, round_number)
    if not rnd:
        raise HTTPException(404, "Round not found")
    if rnd["status"] == "closed":
        raise HTTPException(400, "该轮已关闭")

    # 检查前序轮次是否完成
    if round_number > 1:
        prev = get_round(simulation_id, round_number - 1)
        if prev and prev["status"] != "closed":
            raise HTTPException(400, f"第{round_number - 1}轮尚未完成")

    result = await run_round(simulation_id, round_number, llm_call=llm_call if LLM_ENABLED else None)

    if "error" in result:
        raise HTTPException(400, result["error"])

    return result


@router.post("/{simulation_id}/rounds/{round_number}/retry")
async def api_retry_round(
    simulation_id: str,
    round_number: int,
    user: dict = Depends(get_current_user)
):
    """重试失败的 Agent 采集"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    rnd = get_round(simulation_id, round_number)
    if not rnd:
        raise HTTPException(404, "Round not found")
    if rnd["status"] == "closed":
        raise HTTPException(400, "该轮已关闭，无法重试")

    # 删除失败的 reactions，重新执行
    from database import get_db
    conn = get_db()
    conn.execute(
        "DELETE FROM round_reactions WHERE round_id = ? AND status = 'failed'",
        (rnd["round_id"],)
    )
    conn.commit()
    conn.close()

    result = await run_round(simulation_id, round_number, llm_call=llm_call if LLM_ENABLED else None)

    if "error" in result:
        raise HTTPException(400, result["error"])

    return result


@router.post("/{simulation_id}/rounds/{round_number}/close")
async def api_close_round(
    simulation_id: str,
    round_number: int,
    user: dict = Depends(get_current_user)
):
    """手动关闭轮次（即使有失败的采集）"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")

    rnd = get_round(simulation_id, round_number)
    if not rnd:
        raise HTTPException(404, "Round not found")
    if rnd["status"] == "closed":
        raise HTTPException(400, "该轮已关闭")

    from simulation import aggregate_round, update_round, update_simulation, _update_final_stances, _get_all_summaries, _now, get_round as get_rnd

    aggregated = aggregate_round(rnd["round_id"])
    update_round(rnd["round_id"], status="closed", closes_at=_now(), aggregated_result=aggregated)

    if round_number == sim["total_rounds"]:
        _update_final_stances(simulation_id, rnd["round_id"])
        import json
        update_simulation(simulation_id, status="closed", closes_at=_now(),
                          final_prediction=json.dumps(aggregated.get("prediction", {})))

    if round_number < sim["total_rounds"]:
        all_summaries = _get_all_summaries(simulation_id)
        next_rnd = get_rnd(simulation_id, round_number + 1)
        if next_rnd:
            update_round(next_rnd["round_id"], context="\n\n".join(all_summaries))

    return {"success": True, "message": f"第{round_number}轮已手动关闭"}


@router.get("/{simulation_id}/rounds/{round_number}/reactions")
async def api_get_round_reactions(
    simulation_id: str,
    round_number: int
):
    """获取某轮所有反应"""
    rnd = get_round(simulation_id, round_number)
    if not rnd:
        raise HTTPException(404, "Round not found")
    return {"reactions": get_round_reactions(rnd["round_id"])}


# ==========================================
# Dispute
# ==========================================

@router.post("/{simulation_id}/reactions/{reaction_id}/dispute")
async def api_dispute_reaction(
    simulation_id: str,
    reaction_id: str,
    data: DisputeRequest,
    user: dict = Depends(get_current_user)
):
    """标记反应不准确"""
    result = dispute_reaction(
        reaction_id=reaction_id,
        user_id=user["user_id"],
        correction_stance=data.correction_stance,
        correction_confidence=data.correction_confidence,
        correction_text=data.correction_text,
        reason=data.reason
    )

    if "error" in result:
        if result["error"] == "not_found":
            raise HTTPException(404, "Reaction not found")
        elif result["error"] == "forbidden":
            raise HTTPException(403, result.get("message", "无权操作"))
        else:
            raise HTTPException(400, result.get("message", result["error"]))

    return result


# ==========================================
# Lifecycle
# ==========================================

@router.post("/{simulation_id}/resolve")
async def api_resolve(
    simulation_id: str,
    data: ResolveRequest,
    user: dict = Depends(get_current_user)
):
    """判定结果 + 结算"""
    sim = get_simulation(simulation_id)
    if not sim:
        raise HTTPException(404, "Simulation not found")
    if sim["created_by"] != user["user_id"]:
        raise HTTPException(403, "无权操作")
    if sim["status"] not in ("closed", "resolved"):
        raise HTTPException(400, f"当前状态 {sim['status']} 不支持判定")

    # 验证 outcome
    options = sim.get("outcome_options", ["yes", "no"])
    if isinstance(options, str):
        options = json.loads(options)
    if data.actual_outcome not in options:
        raise HTTPException(400, f"结果必须是 {options} 之一")

    result = settle_simulation(simulation_id, data.actual_outcome)

    if "error" in result:
        raise HTTPException(400, result["error"])

    return result


# ==========================================
# Scores & Leaderboard
# ==========================================

@router.get("/{simulation_id}/leaderboard")
async def api_sim_leaderboard(simulation_id: str):
    """该 Simulation 的参与者排名"""
    participants = get_participants(simulation_id)
    # 按 reward_amount 排序
    ranked = sorted(participants, key=lambda p: p.get("reward_amount") or 0, reverse=True)
    return {"participants": ranked}


# ==========================================
# 独立路由（不在 /simulations 下）
# ==========================================

agent_sim_router = APIRouter(tags=["simulation"])


@agent_sim_router.get("/api/agents/{agent_id}/sim-score")
async def api_agent_sim_score(agent_id: str):
    """Agent 的 Simulation 历史评分"""
    from database import get_db
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.*, a.name as agent_name, a.agent_type
        FROM agent_sim_scores s
        JOIN agents a ON s.agent_id = a.agent_id
        WHERE s.agent_id = ?
    """, (agent_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {
            "agent_id": agent_id,
            "total_participated": 0,
            "message": "暂无 Simulation 参与记录"
        }

    result = dict(row)
    if result.get("accuracy_by_category") and isinstance(result["accuracy_by_category"], str):
        try:
            raw = json.loads(result["accuracy_by_category"])
            result["accuracy_by_category"] = {k: v for k, v in raw.items() if not k.startswith("__")}
        except:
            pass
    return result


@agent_sim_router.get("/api/agents/{agent_id}/reactions")
async def api_agent_reactions(
    agent_id: str,
    user: dict = Depends(get_current_user)
):
    """Agent 的所有历史 React 记录"""
    from database import get_db
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT owner_id FROM agents WHERE agent_id = ?", (agent_id,))
    agent = cursor.fetchone()
    conn.close()

    if not agent:
        raise HTTPException(404, "Agent not found")
    if agent["owner_id"] != user["user_id"]:
        raise HTTPException(403, "只能查看自己 Agent 的反应")

    reactions = get_agent_reactions(agent_id)

    total = len(reactions)
    disputed = sum(1 for r in reactions if r.get("owner_disputed"))
    dispute_rate = disputed / total if total > 0 else 0

    return {
        "reactions": reactions,
        "total": total,
        "disputed": disputed,
        "dispute_rate": round(dispute_rate, 3),
        "auto_accuracy": f"{1 - dispute_rate:.0%}" if total > 0 else "N/A"
    }


@agent_sim_router.get("/api/simulation-leaderboard")
async def api_leaderboard(limit: int = Query(20, ge=1, le=100)):
    """全局排行榜"""
    return {"leaderboard": get_leaderboard(limit)}
