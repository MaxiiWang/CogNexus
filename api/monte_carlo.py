"""
Monte Carlo Cognitive Simulation Engine
Few-shot sampling + statistical extrapolation
"""
import json
import random
import uuid
import re
from typing import Dict, List, Optional
from datetime import datetime
from database import get_db


def _gen_id(prefix="arc"):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now():
    return datetime.now().isoformat()


# =============================================
# Phase 1: Task Analysis - LLM determines archetypes
# =============================================

async def analyze_task(simulation_id: str, llm_call) -> Dict:
    """LLM analyzes the simulation question and determines archetypes."""
    from simulation import get_simulation, get_participants

    sim = get_simulation(simulation_id)
    participants = get_participants(simulation_id)

    options = sim.get("outcome_options", ["yes", "no"])
    if isinstance(options, str):
        options = json.loads(options)

    agents_desc = "\n".join([
        f"- {p.get('agent_name', p['agent_id'])}: {p.get('role', '')} ({p['agent_type']}, {p.get('role_description', '')})"
        for p in participants
    ])

    system = """你是认知模拟系统的任务分析器。分析模拟问题，确定认知原型和权重分配。

输出严格JSON:
{
  "target_population": <number, 总模拟人数>,
  "dimensions": ["维度1", "维度2"],
  "archetypes": [
    {
      "name": "原型名称",
      "description": "一句话描述这类人的认知特征",
      "weight": <0-1, 在总人口中的占比>,
      "mapped_agent_id": "<最匹配的已有agent_id，无则null>",
      "sample_count": <3-5, 建议采样次数>
    }
  ]
}

注意:
- 所有原型的 weight 之和必须 = 1.0
- 尽量把已有 Agent 映射到原型
- 未被映射的原型将使用 LLM 直接模拟
- sample_count 通常 3-5，观点可能分散的原型用 5"""

    mc_config = json.loads(sim.get('monte_carlo_config', '{}') or '{}')

    user = f"""模拟问题: {sim['question']}
类别: {sim['category']}
描述: {sim.get('description', '')}
可选结果: {json.dumps(options)}

已有 Agent:
{agents_desc}

蒙特卡洛配置:
{json.dumps(mc_config, ensure_ascii=False)}"""

    try:
        result = await llm_call(system, user)
        analysis = json.loads(result) if isinstance(result, str) else result
        return analysis
    except Exception:
        # Default: one archetype per agent
        return {
            "target_population": mc_config.get("target_population", 100),
            "dimensions": ["general"],
            "archetypes": [
                {
                    "name": p.get("agent_name", p["agent_id"]),
                    "description": p.get("role_description", ""),
                    "weight": round(1.0 / max(len(participants), 1), 2),
                    "mapped_agent_id": p["agent_id"],
                    "sample_count": 3
                }
                for p in participants
            ]
        }


def save_archetypes(simulation_id: str, analysis: Dict) -> List[Dict]:
    """Save archetypes to database."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM simulation_archetypes WHERE simulation_id = ?", (simulation_id,))

    target_pop = analysis.get("target_population", 100)
    archetypes = analysis.get("archetypes", [])
    saved = []

    for arc in archetypes:
        arc_id = _gen_id("arc")
        pop_count = int(target_pop * arc.get("weight", 0.2))
        cursor.execute("""
            INSERT INTO simulation_archetypes
            (archetype_id, simulation_id, name, description, weight, population_count,
             mapped_agent_id, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (arc_id, simulation_id, arc["name"], arc.get("description", ""),
              arc.get("weight", 0.2), pop_count,
              arc.get("mapped_agent_id"), arc.get("sample_count", 3)))
        saved.append({"archetype_id": arc_id, **arc, "population_count": pop_count})

    conn.commit()
    conn.close()
    return saved


# =============================================
# Phase 2: Few-shot Sampling with Perturbation
# =============================================

def generate_perturbations(archetype: Dict, sample_count: int) -> List[Dict]:
    """Generate perturbation configs for each sample."""
    perturbations = []
    for i in range(sample_count):
        perturbations.append({
            "seed": i,
            "temperature": round(random.uniform(0.4, 1.1), 2),
            "sub_persona": _generate_sub_persona(archetype, i, sample_count),
            "time_offset": random.choice(["即时反应", "深思熟虑后", "事后回顾"]),
        })
    return perturbations


def _generate_sub_persona(archetype: Dict, index: int, total: int) -> str:
    """Generate sub-persona variation for diversity."""
    variations = {
        0: "你是这个群体中比较保守谨慎的代表",
        1: "你是这个群体中比较激进大胆的代表",
        2: "你是这个群体中的主流中间派",
        3: "你是这个群体中经验丰富的资深成员",
        4: "你是这个群体中相对年轻的新成员",
    }
    return variations.get(index, f"你是这个群体中的第{index+1}类代表")


async def collect_monte_carlo_samples(
    simulation_id: str,
    round_number: int,
    llm_call,
    timeout: int = 120
) -> Dict:
    """
    For each archetype, collect few-shot samples with perturbation.
    Uses real Agent endpoint if available, otherwise LLM direct.
    """
    from simulation import (
        get_simulation, get_round, get_participants,
        collect_single_reaction, update_reaction, update_round,
        _call_sim_llm
    )
    from crypto_utils import decrypt_api_key

    sim = get_simulation(simulation_id)
    rnd = get_round(simulation_id, round_number)
    participants = get_participants(simulation_id)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM simulation_archetypes WHERE simulation_id = ?", (simulation_id,))
    archetypes = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not archetypes:
        return {"error": "no_archetypes", "message": "请先运行任务分析"}

    agent_map = {p["agent_id"]: p for p in participants}

    options = sim.get("outcome_options", ["yes", "no"])
    if isinstance(options, str):
        options = json.loads(options)
    options_str = " / ".join(options)

    # Get LLM config for fallback
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT llm_base_url, llm_api_key_enc, llm_model FROM simulations WHERE simulation_id=?", (simulation_id,))
    lr = cursor.fetchone()
    conn.close()

    all_samples = []

    update_round(rnd["round_id"], status="active", opens_at=_now())

    for arc in archetypes:
        sample_count = arc.get("sample_count", 3)
        perturbations = generate_perturbations(arc, sample_count)
        agent = agent_map.get(arc.get("mapped_agent_id"))

        for perturb in perturbations:
            prompt = f"""你现在代表「{arc['name']}」这个群体。{arc.get('description', '')}
{perturb['sub_persona']}
反应时机: {perturb['time_offset']}

关于「{sim['question']}」，请给出你的判断。

---PREDICTION---
{{"stance": "<{options_str}>", "confidence": <0.0-1.0>, "brief_reasoning": "<一句话理由>"}}"""

            prompt_data = {
                "agent_id": arc.get("mapped_agent_id", "virtual"),
                "prompt_type": "predictive",
                "prompt": prompt
            }

            # Use unique virtual agent_id per sample to avoid UNIQUE(round_id, agent_id) constraint
            virtual_agent_id = f"virtual_{arc['archetype_id']}_{perturb['seed']}"

            # Insert reaction directly (bypassing insert_reaction to use virtual agent_id)
            reaction_id = _gen_id("rxn")
            import time as _time
            for _attempt in range(5):
                _conn = None
                try:
                    _conn = get_db()
                    _conn.execute("""
                        INSERT INTO round_reactions
                        (reaction_id, round_id, simulation_id, agent_id,
                         prompt, prompt_type, status, archetype_id, perturbation_seed, is_monte_carlo)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 'true')
                    """, (reaction_id, rnd["round_id"], simulation_id, virtual_agent_id,
                          prompt, "predictive", arc["archetype_id"], json.dumps(perturb)))
                    _conn.commit()
                    _conn.close()
                    break
                except Exception as _e:
                    if _conn:
                        try: _conn.close()
                        except: pass
                    if "locked" in str(_e) and _attempt < 4:
                        _time.sleep(0.5 * (_attempt + 1))
                        continue
                    raise

            # Collect: try real agent first, then LLM
            result = None
            if agent:
                result = await collect_single_reaction(agent, prompt_data, sim, rnd, timeout=timeout)

            if not result or result.get("status") != "collected":
                # LLM fallback
                try:
                    if lr and lr["llm_api_key_enc"]:
                        api_key = decrypt_api_key(lr["llm_api_key_enc"])
                        system = f"你是「{arc['name']}」。{arc.get('description', '')} {perturb['sub_persona']}"
                        llm_response = await _call_sim_llm(lr["llm_base_url"], api_key, lr["llm_model"], system, prompt)

                        if llm_response:
                            json_match = re.search(r'\{[^{}]*"stance"[^{}]*\}', llm_response, re.DOTALL)
                            result = {
                                "status": "collected",
                                "response_text": llm_response,
                                "stance": options[0],
                                "confidence": 0.5,
                                "brief_reasoning": ""
                            }
                            if json_match:
                                try:
                                    pred = json.loads(json_match.group())
                                    if pred.get("stance") in options:
                                        result["stance"] = pred["stance"]
                                    result["confidence"] = max(0, min(1, float(pred.get("confidence", 0.5))))
                                    result["brief_reasoning"] = pred.get("brief_reasoning", "")
                                except Exception:
                                    pass
                except Exception as e:
                    result = {"status": "failed", "error": str(e)[:100]}

            # Update reaction
            if result and result.get("status") == "collected":
                update_reaction(reaction_id,
                    status="collected",
                    collected_at=_now(),
                    response_text=result.get("response_text", ""),
                    stance=result.get("stance", ""),
                    confidence=result.get("confidence", 0.5),
                    brief_reasoning=result.get("brief_reasoning", ""))
            else:
                update_reaction(reaction_id, status="failed")

            all_samples.append({
                "archetype": arc["name"],
                "archetype_id": arc["archetype_id"],
                "perturbation": perturb,
                "status": result.get("status", "failed") if result else "failed",
                "stance": result.get("stance") if result else None,
                "confidence": result.get("confidence") if result else None,
                "reaction_id": reaction_id
            })

    return {
        "total_samples": len(all_samples),
        "collected": sum(1 for s in all_samples if s["status"] == "collected"),
        "failed": sum(1 for s in all_samples if s["status"] == "failed"),
        "samples": all_samples
    }


# =============================================
# Phase 3: Statistical Extrapolation
# =============================================

def extrapolate_results(simulation_id: str, round_id: str) -> Dict:
    """
    From few-shot samples, extrapolate to full population distribution.
    For each archetype: compute stance distribution, estimate confidence stats,
    generate virtual population counts using proportions.
    Then aggregate across archetypes with weights.
    """
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM simulation_archetypes WHERE simulation_id = ?", (simulation_id,))
    archetypes = [dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT rr.*, sa.weight, sa.population_count, sa.name as archetype_name
        FROM round_reactions rr
        JOIN simulation_archetypes sa ON rr.archetype_id = sa.archetype_id
        WHERE rr.round_id = ? AND rr.is_monte_carlo = 'true' AND rr.status = 'collected'
    """, (round_id,))
    samples = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not samples:
        return {"error": "no_samples"}

    # Group samples by archetype
    by_archetype = {}
    for s in samples:
        aid = s.get("archetype_id", "unknown")
        if aid not in by_archetype:
            by_archetype[aid] = {
                "name": s.get("archetype_name", "?"),
                "weight": s.get("weight", 0.2),
                "population_count": s.get("population_count", 20),
                "samples": []
            }
        by_archetype[aid]["samples"].append(s)

    archetype_results = []
    total_population = 0
    overall_stance_counts = {}

    for aid, data in by_archetype.items():
        samples_list = data["samples"]
        pop = data["population_count"] or 20
        total_population += pop

        # Stance distribution from samples
        stance_counts = {}
        confidences = []
        for s in samples_list:
            st = s.get("stance")
            if st:
                stance_counts[st] = stance_counts.get(st, 0) + 1
                confidences.append(s.get("confidence", 0.5))

        total_s = sum(stance_counts.values()) or 1
        stance_dist = {k: round(v / total_s, 3) for k, v in stance_counts.items()}

        conf_mean = sum(confidences) / len(confidences) if confidences else 0.5
        conf_std = (sum((c - conf_mean)**2 for c in confidences) / len(confidences))**0.5 if len(confidences) > 1 else 0.1

        # Extrapolate to population
        extrapolated_stances = {}
        for stance, proportion in stance_dist.items():
            count = round(pop * proportion)
            extrapolated_stances[stance] = count
            overall_stance_counts[stance] = overall_stance_counts.get(stance, 0) + count

        variance = conf_std + (1 - max(stance_dist.values(), default=0.5))

        # Update archetype in DB
        conn2 = get_db()
        conn2.execute("""
            UPDATE simulation_archetypes SET
                stance_distribution = ?, confidence_mean = ?, confidence_std = ?, variance_score = ?
            WHERE archetype_id = ?
        """, (json.dumps(stance_dist), round(conf_mean, 3), round(conf_std, 3),
              round(variance, 3), aid))
        conn2.commit()
        conn2.close()

        archetype_results.append({
            "archetype_id": aid,
            "name": data["name"],
            "weight": data["weight"],
            "population_count": pop,
            "sample_count": len(samples_list),
            "stance_distribution": stance_dist,
            "confidence_mean": round(conf_mean, 3),
            "confidence_std": round(conf_std, 3),
            "variance_score": round(variance, 3),
            "extrapolated_stances": extrapolated_stances,
        })

    overall_total = sum(overall_stance_counts.values()) or 1
    overall_distribution = {k: round(v / overall_total, 4) for k, v in overall_stance_counts.items()}

    top_stance = max(overall_distribution, key=overall_distribution.get) if overall_distribution else None

    # Find divergence points
    divergence_points = []
    for ar in archetype_results:
        ar_top = max(ar["stance_distribution"], key=ar["stance_distribution"].get) if ar["stance_distribution"] else None
        if ar_top and ar_top != top_stance:
            divergence_points.append({
                "archetype": ar["name"],
                "majority_says": top_stance,
                "archetype_says": ar_top,
                "archetype_confidence": ar["confidence_mean"]
            })

    edge_voices = [ar for ar in archetype_results if ar["variance_score"] > 0.6]

    return {
        "total_population": total_population,
        "total_samples": len(samples),
        "overall_distribution": overall_distribution,
        "top_stance": top_stance,
        "top_probability": overall_distribution.get(top_stance, 0) if top_stance else 0,
        "archetype_results": archetype_results,
        "divergence_points": divergence_points,
        "edge_voices": [{"archetype": e["name"], "variance": e["variance_score"]} for e in edge_voices],
        "methodology": "monte_carlo_few_shot_extrapolation",
        "efficiency": f"{len(samples)} LLM calls → {total_population} simulated responses"
    }


# =============================================
# Orchestrator: Full Monte Carlo Round
# =============================================

async def run_monte_carlo_round(
    simulation_id: str,
    round_number: int,
    environment_injection: str = ""
) -> Dict:
    """
    Full Monte Carlo round:
    1. Collect few-shot samples with perturbation
    2. Extrapolate to population distribution
    3. Generate summary
    """
    from simulation import (
        get_simulation, get_round, update_round, update_simulation,
        _call_sim_llm
    )
    from crypto_utils import decrypt_api_key

    sim = get_simulation(simulation_id)
    rnd = get_round(simulation_id, round_number)

    if not sim or not rnd:
        return {"error": "not_found"}

    if environment_injection:
        update_round(rnd["round_id"], environment_injection=environment_injection)

    # Build LLM caller
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT llm_base_url, llm_api_key_enc, llm_model FROM simulations WHERE simulation_id=?", (simulation_id,))
    lr = cursor.fetchone()
    conn.close()

    async def llm_call(system, user):
        if lr and lr["llm_api_key_enc"]:
            api_key = decrypt_api_key(lr["llm_api_key_enc"])
            return await _call_sim_llm(lr["llm_base_url"], api_key, lr["llm_model"], system, user)
        return None

    # Phase 1: If no archetypes yet, analyze task
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM simulation_archetypes WHERE simulation_id = ?", (simulation_id,))
    arc_count = cursor.fetchone()[0]
    conn.close()

    if arc_count == 0:
        analysis = await analyze_task(simulation_id, llm_call)
        save_archetypes(simulation_id, analysis)

    # Phase 2: Collect samples
    update_simulation(simulation_id, status="active", current_round=round_number)
    sample_result = await collect_monte_carlo_samples(simulation_id, round_number, llm_call)

    if sample_result.get("error"):
        return sample_result

    # Phase 3: Extrapolate
    extrapolation = extrapolate_results(simulation_id, rnd["round_id"])

    # Generate summary
    summary_parts = [f"第{round_number}轮 蒙特卡洛模拟"]
    summary_parts.append(f"采样: {sample_result['collected']}/{sample_result['total_samples']} 成功")
    if extrapolation.get("overall_distribution"):
        dist_str = ", ".join([f"{k}: {v:.0%}" for k, v in extrapolation["overall_distribution"].items()])
        summary_parts.append(f"总体分布 ({extrapolation['total_population']}人): {dist_str}")
    if extrapolation.get("divergence_points"):
        for dp in extrapolation["divergence_points"]:
            summary_parts.append(f"分歧: {dp['archetype']}认为{dp['archetype_says']}，与主流{dp['majority_says']}相反")

    summary = "\n".join(summary_parts)

    all_collected = sample_result["failed"] == 0
    status = "closed" if all_collected else "active"

    update_round(rnd["round_id"],
        status=status,
        closes_at=_now() if status == "closed" else None,
        aggregated_result=extrapolation,
        result_summary=summary if all_collected else summary + f"\n⚠️ {sample_result['failed']} 个采样失败"
    )

    if status == "closed" and round_number == sim["total_rounds"]:
        update_simulation(simulation_id, status="closed", closes_at=_now(),
                          final_prediction=json.dumps(extrapolation.get("overall_distribution", {})))

    return {
        "round_number": round_number,
        "sampling": sample_result,
        "extrapolation": extrapolation,
        "summary": summary,
        "status": status
    }
