"""
Cleanup Scan Runner — 清理建议
检测长期未使用的孤立知识条目（SQLite + Neo4j，无 LLM）
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
            "summary": f"{len(candidates)} 条知识条目超过 {cleanup_days} 天未使用且无关联",
            "metadata": {
                "candidates_count": len(candidates),
                "cleanup_days": cleanup_days,
            }
        }

    def _find_candidates(self, namespace: str, cleanup_days: int) -> List[Dict]:
        from cogmate_core.config import get_sqlite, get_neo4j

        conn = get_sqlite()
        cursor = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=cleanup_days)).isoformat()

        # Step 1: Long unused facts
        cursor.execute("""
            SELECT fact_id, summary, content_type, source_type, source_url, created_at,
                   last_retrieved_at, retrieval_count
            FROM facts
            WHERE namespace = ?
              AND (last_retrieved_at IS NULL OR last_retrieved_at < ?)
              AND created_at < ?
            ORDER BY created_at ASC
        """, (namespace, cutoff, cutoff))

        candidates = [
            {
                'fact_id': r[0], 'summary': r[1], 'content_type': r[2],
                'source_type': r[3], 'source_url': r[4], 'created_at': r[5],
                'last_retrieved_at': r[6], 'retrieval_count': r[7] or 0,
            }
            for r in cursor.fetchall()
        ]
        conn.close()

        if not candidates:
            return []

        # Step 2: Filter to orphan nodes only (no edges in graph)
        driver = get_neo4j()
        orphans = []
        try:
            with driver.session() as session:
                for c in candidates:
                    result = session.run("""
                        MATCH (f:Fact {fact_id: $fid})
                        WHERE NOT (f)-[]-()
                        RETURN f.fact_id AS fid
                    """, fid=c['fact_id'])
                    if result.single():
                        orphans.append(c)
        except Exception as e:
            print(f"[CleanupScan] Neo4j error: {e}")
            # If Neo4j fails, still return candidates (less strict filter)
            orphans = candidates

        # Step 3: Exclude facts referenced by abstractions
        try:
            conn2 = get_sqlite()
            cursor2 = conn2.cursor()
            final = []
            for c in orphans:
                cursor2.execute(
                    "SELECT 1 FROM abstracts WHERE source_fact_ids LIKE ?",
                    (f'%{c["fact_id"]}%',)
                )
                if not cursor2.fetchone():
                    final.append(c)
            conn2.close()
            return final[:20]  # Cap at 20
        except Exception:
            return orphans[:20]

    def _format(self, candidates, cleanup_days):
        lines = [f"# 🗑️ 清理建议\n"]
        lines.append(f"以下 **{len(candidates)}** 条知识条目超过 {cleanup_days} 天未被检索，且无任何图谱关联：\n")

        for i, c in enumerate(candidates[:15], 1):
            fid = c['fact_id'][:8]
            date = (c['created_at'] or '')[:10]
            source = c.get('source_type', 'unknown')
            summary = (c.get('summary') or '')[:60]
            retrievals = c.get('retrieval_count', 0)

            lines.append(f"**{i}.** `{fid}` | {date} | 来源: {source} | 检索: {retrievals}次")
            lines.append(f"   {summary}...")
            lines.append("")

        if len(candidates) > 15:
            lines.append(f"... 还有 {len(candidates) - 15} 条\n")

        lines.append("---")
        lines.append("*这些条目可能已失去参考价值。可在知识库中审查并决定保留或删除。*")

        return "\n".join(lines)
