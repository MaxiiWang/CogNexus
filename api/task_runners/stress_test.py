"""
Stress Test Runner — 观点挑战
识别核心判断，搜索反对意见，LLM 生成挑战问题
"""
import json
from datetime import datetime
from typing import List, Dict
from .base import BaseTaskRunner
from .briefing import brave_search, call_llm_async


def _get_core_beliefs(namespace: str, limit: int = 10) -> List[Dict]:
    """获取核心判断（观点/决策类，按图谱权重排序）"""
    from cogmate_core.config import get_sqlite, get_neo4j

    conn = get_sqlite()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT fact_id, summary, content_type, context, created_at
        FROM facts
        WHERE namespace = ? AND content_type IN ('观点', '决策')
        ORDER BY created_at DESC
    """, (namespace,))
    candidates = [
        {'fact_id': r[0], 'content': r[1], 'content_type': r[2], 'context': r[3], 'created_at': r[4]}
        for r in cursor.fetchall()
    ]
    conn.close()

    if not candidates:
        return []

    # Weight by graph degree
    driver = get_neo4j()
    weighted = []
    try:
        with driver.session() as session:
            for c in candidates:
                result = session.run(
                    'MATCH (n:Fact {fact_id: $fid})-[r]-() RETURN count(r) as degree',
                    fid=c['fact_id']
                )
                record = result.single()
                degree = record['degree'] if record else 0
                c['weight'] = degree
                if degree > 0:
                    weighted.append(c)
    except Exception as e:
        print(f"[StressTest] Neo4j error: {e}")
        weighted = candidates[:limit]

    weighted.sort(key=lambda x: x.get('weight', 0), reverse=True)
    return weighted[:limit]


class StressTestRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        cogmate = self.get_cogmate(agent_id)
        ns = cogmate.namespace
        llm_config = self.get_llm_config(agent_id)
        challenge_count = config.get('challenge_count', 3)

        # 1. Select targets
        beliefs = _get_core_beliefs(ns, limit=challenge_count * 2)
        if not beliefs:
            return {
                "title": "⚔️ 观点挑战",
                "content": "知识库中还没有足够的「观点」或「决策」类记录。多积累一些判断性内容后再来挑战！",
                "summary": "无可挑战的核心观点",
                "metadata": {"challenges_count": 0}
            }

        targets = beliefs[:challenge_count]

        # 2. Search opposing views
        for t in targets:
            keywords = t['content'][:60]
            results = await brave_search(f"{keywords} 反对 OR 质疑 OR 争议 OR 不同观点", count=3)
            t['opposing'] = results

        # 3. One LLM call to generate challenge questions
        challenges_text = await self._generate_challenges(llm_config, targets)

        # 4. Format
        if not challenges_text:
            # Fallback without LLM
            challenges_text = self._format_fallback(targets)

        today = datetime.now().strftime("%Y-%m-%d")
        return {
            "title": f"⚔️ 观点挑战 · {today}",
            "content": challenges_text,
            "summary": f"{len(targets)} 个核心判断受到挑战",
            "metadata": {
                "challenges_count": len(targets),
                "beliefs_scanned": len(beliefs),
            }
        }

    async def _generate_challenges(self, llm_config: dict, targets: list) -> str:
        parts = []
        for i, t in enumerate(targets, 1):
            opposing_text = ""
            if t.get('opposing'):
                opposing_text = "\n".join([f"  - {o['title']}: {o['description'][:100]}" for o in t['opposing'][:2]])
            parts.append(f"""核心判断 {i}：{t['content'][:200]}
来源：{t['fact_id'][:8]} ({t.get('created_at', '')[:10]})
图谱权重：{t.get('weight', 0)}
外部反对意见：
{opposing_text or '  （未找到明确反对意见）'}""")

        raw = "\n\n".join(parts)

        prompt = f"""你是一个批判性思考助手。以下是用户知识库中权重最高的核心判断，以及搜索到的外部反对意见。

请对每个判断生成一个深度挑战问题。

要求：
- 如果有反对意见，问题要直击反对观点的核心
- 如果没有反对意见，问题要探索该判断的边界条件或盲点
- 问题要具体、有深度、能引发真正的思考
- 不要泛泛而谈

{raw}

严格按以下格式输出：

# ⚔️ 观点挑战

🎯 **挑战 1**
- 核心判断：（简述）
- 来源：`fact_id` (日期)
- 外部声音：（如有）
- 💬 挑战问题

🎯 **挑战 2**
...

直接输出："""

        return await call_llm_async(llm_config, prompt, max_tokens=1500)

    def _format_fallback(self, targets: list) -> str:
        lines = ["# ⚔️ 观点挑战\n"]
        lines.append("> ⚠️ LLM 暂不可用，以下为原始数据\n")
        for i, t in enumerate(targets, 1):
            lines.append(f"## 🎯 挑战 {i}\n")
            lines.append(f"**核心判断**：{t['content'][:100]}...")
            lines.append(f"来源：`{t['fact_id'][:8]}` ({t.get('created_at', '')[:10]}) | 权重：{t.get('weight', 0)}\n")
            if t.get('opposing'):
                lines.append("**外部声音**：")
                for o in t['opposing'][:2]:
                    lines.append(f"- {o['title'][:60]} — {o['description'][:80]}")
                    lines.append(f"  [来源]({o['url']})")
            else:
                lines.append("**外部声音**：未找到明确反对意见")
            lines.append("")
        return "\n".join(lines)
