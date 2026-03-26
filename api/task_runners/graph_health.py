"""
Graph Health Runner — 图谱健康度
纯 Neo4j 查询，无 LLM
"""
from datetime import datetime
from typing import Dict
from .base import BaseTaskRunner

THRESHOLDS = {
    'orphan_ratio_good': 0.10,
    'orphan_ratio_warn': 0.20,
    'avg_degree_good': 3.0,
    'avg_degree_warn': 2.0,
    'contradiction_ratio_warn': 0.10,
}


def _get_metrics(namespace: str) -> Dict:
    from cogmate_core.config import get_neo4j
    driver = get_neo4j()
    m = {'total_nodes': 0, 'total_edges': 0, 'orphan_count': 0,
         'contradiction_count': 0, 'hub_nodes': [], 'degree_distribution': {}}

    try:
        with driver.session() as session:
            m['total_nodes'] = session.run(
                'MATCH (n:Fact) WHERE n.namespace = $ns RETURN count(n) as c', ns=namespace
            ).single()['c']

            m['total_edges'] = session.run(
                'MATCH (a:Fact)-[r]->(b:Fact) WHERE a.namespace = $ns AND b.namespace = $ns RETURN count(r) as c',
                ns=namespace
            ).single()['c']

            m['orphan_count'] = session.run('''
                MATCH (n:Fact)
                WHERE n.namespace = $ns AND NOT (n)-[]-(:Fact {namespace: $ns})
                RETURN count(n) as c
            ''', ns=namespace).single()['c']

            m['contradiction_count'] = session.run('''
                MATCH (a:Fact)-[r:矛盾]->(b:Fact)
                WHERE a.namespace = $ns AND b.namespace = $ns
                RETURN count(r) as c
            ''', ns=namespace).single()['c']

            # Hub nodes (degree >= 4)
            hub_result = session.run('''
                MATCH (n:Fact)
                WHERE n.namespace = $ns
                OPTIONAL MATCH (n)-[r]-(:Fact {namespace: $ns})
                WITH n, count(r) as degree
                WHERE degree >= 4
                RETURN n.fact_id as id, n.summary as summary, degree
                ORDER BY degree DESC LIMIT 5
            ''', ns=namespace)
            m['hub_nodes'] = [
                {'id': r['id'][:8], 'summary': (r['summary'] or '')[:40], 'degree': r['degree']}
                for r in hub_result
            ]

            # Degree distribution
            deg_result = session.run('''
                MATCH (n:Fact)
                WHERE n.namespace = $ns
                OPTIONAL MATCH (n)-[r]-(:Fact {namespace: $ns})
                WITH n, count(r) as degree
                RETURN degree, count(*) as cnt ORDER BY degree
            ''', ns=namespace)
            m['degree_distribution'] = {r['degree']: r['cnt'] for r in deg_result}

    except Exception as e:
        print(f"[GraphHealth] Neo4j error: {e}")

    # Computed ratios
    n = m['total_nodes'] or 1
    m['orphan_ratio'] = m['orphan_count'] / n
    m['avg_degree'] = (m['total_edges'] * 2) / n
    m['contradiction_ratio'] = m['contradiction_count'] / (m['total_edges'] or 1)

    return m


def _evaluate(m: Dict) -> Dict:
    details = []
    issues = 0

    # Orphan ratio
    r = m['orphan_ratio']
    if r < THRESHOLDS['orphan_ratio_good']:
        s = 'good'
    elif r < THRESHOLDS['orphan_ratio_warn']:
        s = 'warning'; issues += 1
    else:
        s = 'critical'; issues += 1
    details.append({'metric': '孤立节点占比', 'icon': '🟢' if s == 'good' else '🟡' if s == 'warning' else '🔴',
                    'value': f"{r:.1%}", 'target': f"<{THRESHOLDS['orphan_ratio_warn']:.0%}"})

    # Avg degree
    d = m['avg_degree']
    if d >= THRESHOLDS['avg_degree_good']:
        s = 'good'
    elif d >= THRESHOLDS['avg_degree_warn']:
        s = 'warning'; issues += 1
    else:
        s = 'critical'; issues += 1
    details.append({'metric': '平均节点度数', 'icon': '🟢' if s == 'good' else '🟡' if s == 'warning' else '🔴',
                    'value': f"{d:.2f}", 'target': f">{THRESHOLDS['avg_degree_warn']:.1f}"})

    # Contradiction ratio
    c = m['contradiction_ratio']
    s = 'good' if c < THRESHOLDS['contradiction_ratio_warn'] else 'warning'
    if s == 'warning': issues += 1
    details.append({'metric': '矛盾关系占比', 'icon': '🟢' if s == 'good' else '🟡',
                    'value': f"{c:.1%}", 'target': f"<{THRESHOLDS['contradiction_ratio_warn']:.0%}"})

    overall = 'critical' if issues >= 2 else 'warning' if issues >= 1 else 'good'
    icon = '🔴' if overall == 'critical' else '🟡' if overall == 'warning' else '🟢'

    return {'overall': overall, 'icon': icon, 'details': details}


class GraphHealthRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        cogmate = self.get_cogmate(agent_id)
        ns = cogmate.namespace
        verbose = config.get('verbose', False)

        m = _get_metrics(ns)
        h = _evaluate(m)

        content = self._format(m, h, verbose)
        today = datetime.now().strftime("%Y-%m-%d")

        return {
            "title": f"🕸️ 图谱健康 · {today}",
            "content": content,
            "summary": f"{h['icon']} {h['overall'].upper()} | {m['total_nodes']}节点 {m['total_edges']}边 孤立{m['orphan_ratio']:.0%}",
            "metadata": {
                "health_overall": h['overall'],
                "total_nodes": m['total_nodes'],
                "total_edges": m['total_edges'],
                "orphan_ratio": round(m['orphan_ratio'], 3),
                "avg_degree": round(m['avg_degree'], 2),
                "contradiction_count": m['contradiction_count'],
            }
        }

    def _format(self, m, h, verbose):
        lines = ["# 🕸️ 图谱健康报告\n"]

        lines.append("## 📊 基础统计\n")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 节点总数 | {m['total_nodes']} |")
        lines.append(f"| 边总数 | {m['total_edges']} |")
        lines.append(f"| 孤立节点 | {m['orphan_count']} |")
        lines.append(f"| 矛盾关系 | {m['contradiction_count']} |")
        lines.append("")

        lines.append("## 📈 健康度指标\n")
        for d in h['details']:
            lines.append(f"- {d['icon']} **{d['metric']}**: {d['value']} (目标 {d['target']})")
        lines.append(f"\n## 🏥 整体健康度: {h['icon']} **{h['overall'].upper()}**\n")

        if m['hub_nodes']:
            lines.append("## 🔗 枢纽节点 (度数≥4)\n")
            for hub in m['hub_nodes']:
                lines.append(f"- `{hub['id']}` 度数:**{hub['degree']}** | {hub['summary']}...")
            lines.append("")

        if verbose and m.get('degree_distribution'):
            lines.append("## 📊 度数分布\n")
            lines.append("```")
            for degree in sorted(m['degree_distribution'].keys()):
                cnt = m['degree_distribution'][degree]
                bar = '█' * min(cnt, 30)
                lines.append(f"度数{degree:2d}: {bar} ({cnt})")
            lines.append("```")

        return "\n".join(lines)
