"""
Graph Health Runner — 图谱健康度（纯 Neo4j，无 LLM）
"""
from datetime import datetime
from .base import BaseTaskRunner

THRESHOLDS = {
    'orphan_good': 0.10,
    'orphan_warn': 0.20,
    'degree_good': 3.0,
    'degree_warn': 2.0,
    'contradiction_warn': 0.10,
}


class GraphHealthRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        cogmate = self.get_cogmate(agent_id)
        ns = cogmate.namespace
        verbose = config.get('verbose', False)

        metrics = self._get_metrics(ns)
        health = self._evaluate(metrics)
        content = self._format(metrics, health, verbose)
        today = datetime.now().strftime("%Y-%m-%d")

        return {
            "title": f"🕸️ 图谱健康 · {today}",
            "content": content,
            "summary": f"{health['icon']} {health['overall']} | {metrics['total_nodes']} 节点, {metrics['total_edges']} 边",
            "metadata": {
                "health_overall": health['overall'],
                "total_nodes": metrics['total_nodes'],
                "total_edges": metrics['total_edges'],
                "orphan_ratio": round(metrics['orphan_ratio'], 3),
                "avg_degree": round(metrics['avg_degree'], 2),
            }
        }

    def _get_metrics(self, ns: str) -> dict:
        from cogmate_core.config import get_neo4j
        driver = get_neo4j()
        m = {'total_nodes': 0, 'total_edges': 0, 'orphan_count': 0,
             'contradiction_count': 0, 'hub_nodes': [], 'degree_dist': {}}
        try:
            with driver.session() as s:
                m['total_nodes'] = s.run(
                    'MATCH (n:Fact) WHERE n.namespace = $ns RETURN count(n) as c', ns=ns
                ).single()['c']
                m['total_edges'] = s.run(
                    'MATCH (a:Fact)-[r]->(b:Fact) WHERE a.namespace = $ns RETURN count(r) as c', ns=ns
                ).single()['c']
                m['orphan_count'] = s.run(
                    'MATCH (n:Fact) WHERE n.namespace = $ns AND NOT (n)-[]-(:Fact {namespace: $ns}) RETURN count(n) as c', ns=ns
                ).single()['c']
                m['contradiction_count'] = s.run(
                    'MATCH (a:Fact)-[r:矛盾]->(b:Fact) WHERE a.namespace = $ns RETURN count(r) as c', ns=ns
                ).single()['c']

                # Hub nodes
                hubs = s.run('''
                    MATCH (n:Fact) WHERE n.namespace = $ns
                    OPTIONAL MATCH (n)-[r]-(:Fact {namespace: $ns})
                    WITH n, count(r) as degree WHERE degree >= 4
                    RETURN n.fact_id as id, n.summary as summary, degree
                    ORDER BY degree DESC LIMIT 5
                ''', ns=ns)
                m['hub_nodes'] = [
                    {'id': r['id'][:8], 'summary': (r['summary'] or '')[:40], 'degree': r['degree']}
                    for r in hubs
                ]

                # Degree distribution
                dist = s.run('''
                    MATCH (n:Fact) WHERE n.namespace = $ns
                    OPTIONAL MATCH (n)-[r]-(:Fact {namespace: $ns})
                    WITH n, count(r) as degree
                    RETURN degree, count(*) as cnt ORDER BY degree
                ''', ns=ns)
                m['degree_dist'] = {r['degree']: r['cnt'] for r in dist}
        except Exception as e:
            print(f"[GraphHealth] Neo4j error: {e}")

        n = m['total_nodes'] or 1
        m['orphan_ratio'] = m['orphan_count'] / n
        m['avg_degree'] = (m['total_edges'] * 2) / n
        m['contradiction_ratio'] = m['contradiction_count'] / max(m['total_edges'], 1)
        return m

    def _evaluate(self, m: dict) -> dict:
        details = []
        issues = 0

        # Orphan ratio
        r = m['orphan_ratio']
        if r < THRESHOLDS['orphan_good']:
            st = '🟢'
        elif r < THRESHOLDS['orphan_warn']:
            st = '🟡'; issues += 1
        else:
            st = '🔴'; issues += 1
        details.append(f"{st} 孤立节点占比: {r:.1%} (目标 <{THRESHOLDS['orphan_warn']:.0%})")

        # Avg degree
        d = m['avg_degree']
        if d >= THRESHOLDS['degree_good']:
            st = '🟢'
        elif d >= THRESHOLDS['degree_warn']:
            st = '🟡'; issues += 1
        else:
            st = '🔴'; issues += 1
        details.append(f"{st} 平均度数: {d:.2f} (目标 >{THRESHOLDS['degree_warn']:.1f})")

        # Contradiction
        c = m['contradiction_ratio']
        st = '🟢' if c < THRESHOLDS['contradiction_warn'] else '🟡'
        details.append(f"{st} 矛盾关系占比: {c:.1%} (目标 <{THRESHOLDS['contradiction_warn']:.0%})")

        overall = 'GOOD' if issues == 0 else 'WARNING' if issues == 1 else 'CRITICAL'
        icon = '🟢' if issues == 0 else '🟡' if issues == 1 else '🔴'
        return {'overall': overall, 'icon': icon, 'details': details}

    def _format(self, m, health, verbose):
        lines = ["# 🕸️ 图谱健康报告\n"]
        lines.append("## 📊 基础统计\n")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 节点总数 | {m['total_nodes']} |")
        lines.append(f"| 边总数 | {m['total_edges']} |")
        lines.append(f"| 孤立节点 | {m['orphan_count']} |")
        lines.append(f"| 矛盾关系 | {m['contradiction_count']} |")
        lines.append("")

        lines.append("## 📈 健康度\n")
        for d in health['details']:
            lines.append(f"- {d}")
        lines.append(f"\n**整体**: {health['icon']} **{health['overall']}**\n")

        if m['hub_nodes']:
            lines.append("## 🔗 枢纽节点 (度数≥4)\n")
            for h in m['hub_nodes']:
                lines.append(f"- `{h['id']}` 度数:**{h['degree']}** | {h['summary']}...")
            lines.append("")

        if verbose and m.get('degree_dist'):
            lines.append("## 📊 度数分布\n")
            lines.append("```")
            for deg in sorted(m['degree_dist']):
                bar = '█' * min(m['degree_dist'][deg], 30)
                lines.append(f"度数{deg:2d}: {bar} ({m['degree_dist'][deg]})")
            lines.append("```")

        return "\n".join(lines)
