"""
Cleanup Scan Runner — 清理建议（SQLite + Neo4j，无 LLM）
"""
from datetime import datetime, timedelta
from typing import List, Dict
from .base import BaseTaskRunner


class CleanupScanRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        cogmate = self.get_cogmate(agent_id)
        ns = cogmate.namespace
        cleanup_days = config.get('cleanup_days', 60)

        candidates = self._find_candidates(ns, cleanup_days)

        if not candidates:
            return None  # Don't create insight

        today = datetime.now().strftime("%Y-%m-%d")
        content = self._format(candidates, cleanup_days)

        return {
            "title": f"🗑️ 清理建议 · {today}",
            "content": content,
            "summary": f"{len(candidates)} 条候选清理项",
            "metadata": {
                "candidates_count": len(candidates),
                "cleanup_days": cleanup_days,
            }
        }

    def _find_candidates(self, namespace: str, cleanup_days: int) -> List[Dict]:
        from cogmate_core.config import get_sqlite, get_neo4j

        cutoff = (datetime.now() - timedelta(days=cleanup_days)).isoformat()

        # Step 1: Long unused facts from SQLite
        conn = get_sqlite()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fact_id, summary, content_type, source_type, created_at, last_retrieved_at, retrieval_count
            FROM facts
            WHERE namespace = ?
              AND (last_retrieved_at IS NULL OR last_retrieved_at < ?)
              AND created_at < ?
            ORDER BY created_at ASC
        """, (namespace, cutoff, cutoff))
        candidates = [
            {'fact_id': r[0], 'summary': r[1], 'content_type': r[2], 'source_type': r[3],
             'created_at': r[4], 'last_retrieved_at': r[5], 'retrieval_count': r[6] or 0}
            for r in cursor.fetchall()
        ]
        conn.close()

        if not candidates:
            return []

        # Step 2: Keep only orphan nodes (no edges in graph)
        driver = get_neo4j()
        orphans = []
        try:
            with driver.session() as session:
                for c in candidates:
                    result = session.run(
                        'MATCH (f:Fact {fact_id: $fid}) WHERE NOT (f)-[]-() RETURN f.fact_id as fid',
                        fid=c['fact_id']
                    )
                    if result.single():
                        orphans.append(c)
        except Exception as e:
            print(f"[CleanupScan] Neo4j error: {e}")
            return []

        return orphans

    def _format(self, candidates, cleanup_days):
        lines = [f"# 🗑️ 清理建议\n"]
        lines.append(f"以下 **{len(candidates)}** 条知识条目超过 {cleanup_days} 天未被检索，且在图谱中无任何关联：\n")

        for i, c in enumerate(candidates[:15], 1):
            fid = c['fact_id'][:8]
            created = c['created_at'][:10] if c['created_at'] else '?'
            source = c.get('source_type', 'unknown')
            summary = (c['summary'] or '')[:60]
            retrieved = c.get('retrieval_count', 0)
            lines.append(f"**{i}.** `{fid}` | {created} | 来源: {source} | 检索次数: {retrieved}")
            lines.append(f"   {summary}...\n")

        if len(candidates) > 15:
            lines.append(f"... 还有 {len(candidates) - 15} 条\n")

        lines.append("---")
        lines.append("💡 这些条目可能是临时性信息或已失去价值。可在知识库中手动删除或保留。")

        return "\n".join(lines)
