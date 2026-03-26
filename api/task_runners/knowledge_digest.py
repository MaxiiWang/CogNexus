"""
Knowledge Digest Runner — 知识摘要
迁移自 cogmate/lib/daily_report.py
"""
import json
from datetime import datetime
from collections import Counter
from typing import List, Dict
from .base import BaseTaskRunner
from .briefing import call_llm_async


def _get_period_facts(namespace: str, since: str = None) -> List[Dict]:
    """获取指定时间段的事实"""
    from cogmate_core.config import get_sqlite
    if since is None:
        since = datetime.now().strftime("%Y-%m-%d")

    conn = get_sqlite()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT fact_id, summary, content_type, emotion_tag, context, created_at
        FROM facts
        WHERE namespace = ? AND date(created_at) >= date(?)
        ORDER BY created_at DESC
    ''', (namespace, since))

    results = []
    for row in cursor.fetchall():
        results.append({
            'fact_id': row[0],
            'content': row[1],
            'content_type': row[2],
            'emotion': row[3],
            'context': row[4],
            'created_at': row[5]
        })
    conn.close()
    return results


def _get_period_relations(namespace: str, since: str = None) -> List[Dict]:
    """获取指定时间段新增的关联"""
    from cogmate_core.config import get_neo4j
    if since is None:
        since = datetime.now().strftime("%Y-%m-%d")

    driver = get_neo4j()
    relations = []
    try:
        with driver.session() as session:
            result = session.run('''
                MATCH (a:Fact)-[r]->(b:Fact)
                WHERE a.namespace = $ns AND r.created_at >= $since
                RETURN a.fact_id as from_id, b.fact_id as to_id,
                       type(r) as rel_type, r.confidence as confidence
            ''', ns=namespace, since=since)
            for record in result:
                relations.append({
                    'from_id': record['from_id'],
                    'to_id': record['to_id'],
                    'rel_type': record['rel_type'],
                    'confidence': record['confidence']
                })
    except Exception as e:
        print(f"[KD] get_period_relations error: {e}")
    return relations


def _get_graph_stats(namespace: str) -> Dict:
    """获取图谱统计"""
    from cogmate_core.config import get_neo4j
    driver = get_neo4j()
    stats = {'total_nodes': 0, 'total_edges': 0, 'today_nodes': 0}
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        with driver.session() as session:
            stats['total_nodes'] = session.run(
                'MATCH (n:Fact) WHERE n.namespace = $ns RETURN count(n) as c', ns=namespace
            ).single()['c']
            stats['total_edges'] = session.run(
                'MATCH (a:Fact)-[r]->(b:Fact) WHERE a.namespace = $ns RETURN count(r) as c', ns=namespace
            ).single()['c']
            stats['today_nodes'] = session.run(
                'MATCH (n:Fact) WHERE n.namespace = $ns AND n.timestamp STARTS WITH $today RETURN count(n) as c',
                ns=namespace, today=today
            ).single()['c']
    except Exception as e:
        print(f"[KD] get_graph_stats error: {e}")
    return stats


def _get_high_confidence_relations(namespace: str, since: str = None) -> List[Dict]:
    """获取高置信度关联"""
    from cogmate_core.config import get_neo4j
    if since is None:
        since = datetime.now().strftime("%Y-%m-%d")
    driver = get_neo4j()
    relations = []
    try:
        with driver.session() as session:
            result = session.run('''
                MATCH (a:Fact)-[r]->(b:Fact)
                WHERE a.namespace = $ns AND r.created_at >= $since AND r.confidence >= 4
                RETURN a.summary as from_summary, b.summary as to_summary,
                       type(r) as rel_type, r.confidence as confidence
                LIMIT 5
            ''', ns=namespace, since=since)
            for record in result:
                relations.append({
                    'from_summary': (record['from_summary'] or '')[:40],
                    'to_summary': (record['to_summary'] or '')[:40],
                    'rel_type': record['rel_type'],
                    'confidence': record['confidence']
                })
    except Exception as e:
        print(f"[KD] get_high_conf error: {e}")
    return relations


def _get_contradictions(namespace: str, since: str = None) -> List[Dict]:
    """获取矛盾关联"""
    from cogmate_core.config import get_neo4j
    if since is None:
        since = datetime.now().strftime("%Y-%m-%d")
    driver = get_neo4j()
    contradictions = []
    try:
        with driver.session() as session:
            result = session.run('''
                MATCH (a:Fact)-[r:矛盾]->(b:Fact)
                WHERE a.namespace = $ns AND r.created_at >= $since
                RETURN a.summary as from_summary, b.summary as to_summary,
                       r.confidence as confidence
            ''', ns=namespace, since=since)
            for record in result:
                contradictions.append({
                    'from_summary': (record['from_summary'] or '')[:50],
                    'to_summary': (record['to_summary'] or '')[:50],
                    'confidence': record['confidence']
                })
    except Exception as e:
        print(f"[KD] get_contradictions error: {e}")
    return contradictions


def _detect_tensions(cogmate, config: dict) -> List[Dict]:
    """检测今日新内容与存量知识之间的张力"""
    try:
        from cogmate_core.config import get_qdrant, get_embedder, COLLECTION_NAME

        today = datetime.now().strftime("%Y-%m-%d")
        facts = _get_period_facts(cogmate.namespace, since=today)
        if not facts:
            return []

        threshold = config.get('tension_threshold', [0.55, 0.90])
        lo, hi = threshold

        qdrant = get_qdrant()
        embedder = get_embedder()
        today_ids = {f['fact_id'] for f in facts}
        tensions = []

        for fact in facts:
            if fact['content_type'] not in ['观点', '决策']:
                continue

            query_vector = embedder.encode(fact['content']).tolist()
            try:
                response = qdrant.query_points(
                    collection_name=COLLECTION_NAME,
                    query=query_vector,
                    limit=5,
                    score_threshold=lo
                )
                points = response.points if hasattr(response, 'points') else []
                related_old = [r for r in points if r.id not in today_ids]

                if related_old:
                    top = related_old[0]
                    if lo < top.score < hi:
                        tensions.append({
                            'new_fact': fact,
                            'old_fact_id': top.id,
                            'old_content': top.payload.get('summary', '') or top.payload.get('content', ''),
                            'similarity': top.score
                        })
            except Exception:
                continue

        return tensions[:3]
    except Exception as e:
        print(f"[KD] detect_tensions error: {e}")
        return []


class KnowledgeDigestRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        cogmate = self.get_cogmate(agent_id)
        ns = cogmate.namespace
        today = datetime.now().strftime("%Y-%m-%d")
        llm_config = self.get_llm_config(agent_id)

        # Gather data
        facts = _get_period_facts(ns, since=today)
        relations = _get_period_relations(ns, since=today)
        stats = _get_graph_stats(ns)
        high_conf = _get_high_confidence_relations(ns, since=today)
        contradictions = []
        if config.get('include_contradictions', True):
            contradictions = _get_contradictions(ns, since=today)

        tensions = []
        if config.get('include_tensions', True):
            tensions = _detect_tensions(cogmate, config)

        # Generate challenge questions via LLM
        if tensions and config.get('challenge_llm', True) and llm_config:
            for t in tensions:
                t['challenge'] = await self._gen_challenge(llm_config, t)

        # Format report
        content = self._format_report(today, facts, relations, stats, high_conf, contradictions, tensions)
        type_dist = dict(Counter(f['content_type'] for f in facts))

        return {
            "title": f"🧠 知识摘要 · {today}",
            "content": content,
            "summary": f"新增 {len(facts)} 条，{len(relations)} 条关联，{len(tensions)} 个张力点",
            "metadata": {
                "facts_count": len(facts),
                "type_distribution": type_dist,
                "new_edges": len(relations),
                "graph_nodes": stats['total_nodes'],
                "graph_edges": stats['total_edges'],
                "tensions_found": len(tensions),
                "contradictions_found": len(contradictions),
            }
        }

    async def _gen_challenge(self, llm_config: dict, tension: dict) -> str:
        prompt = f"""分析以下两个观点之间的潜在张力，用一句话提出一个挑战性问题：

新观点：{tension['new_fact']['content'][:300]}
旧观点：{tension['old_content'][:300]}

只输出问题本身："""
        result = await call_llm_async(llm_config, prompt, max_tokens=150)
        return result or "这两个观点之间是否需要调和？"

    def _format_report(self, today, facts, relations, stats, high_conf, contradictions, tensions):
        type_counts = Counter(f['content_type'] for f in facts)
        lines = [f"# 🧠 知识摘要 · {today}\n"]

        # New facts
        lines.append(f"## 📥 新增记录: {len(facts)} 条")
        if type_counts:
            lines.append(" | ".join([f"{t} {c}条" for t, c in type_counts.items()]))
        lines.append("")

        # Context
        contexts = [f['context'] for f in facts if f.get('context')]
        if contexts:
            lines.append(f"**今日情境**: {contexts[0][:60]}...")
            lines.append("")

        # High confidence relations
        lines.append("## 💡 值得关注")
        if high_conf:
            for rel in high_conf[:3]:
                lines.append(f"- **[{rel['rel_type']}]** {rel['from_summary']} → {rel['to_summary']}")
        else:
            lines.append("- 无高置信度新关联")
        lines.append("")

        # Contradictions
        if contradictions:
            lines.append(f"## ⚠️ 矛盾发现: {len(contradictions)} 条")
            for c in contradictions[:2]:
                lines.append(f"- {c['from_summary']} ↔ {c['to_summary']}")
            lines.append("")

        # Graph stats
        lines.append("## 🕸️ 图谱状态")
        lines.append(f"- 总节点: {stats['total_nodes']} | 总边: {stats['total_edges']}")
        if stats['today_nodes'] > 0 or relations:
            lines.append(f"- 今日变化: +{stats['today_nodes']} 节点, +{len(relations)} 条边")
        lines.append("")

        # Tensions
        if tensions:
            lines.append(f"## ⚔️ 张力检测: {len(tensions)} 个")
            for i, t in enumerate(tensions, 1):
                new_preview = t['new_fact']['content'][:60]
                old_preview = t['old_content'][:60]
                lines.append(f"\n**张力点 {i}** (相似度 {t['similarity']:.2f})")
                lines.append(f"- 新增: {new_preview}...")
                lines.append(f"- 已有: {old_preview}...")
                challenge = t.get('challenge', '')
                if challenge:
                    lines.append(f"- 💬 {challenge}")
            lines.append("")
        else:
            lines.append("## ⚔️ 张力检测: 无明显张力 ✅\n")

        return "\n".join(lines)
