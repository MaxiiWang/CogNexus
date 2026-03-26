"""
Expiry Scan Runner — 过期检测
扫描即将过期和已过期的知识条目（纯 SQLite，无 LLM）
"""
from datetime import datetime, timedelta
from typing import List, Dict
from .base import BaseTaskRunner


class ExpiryScanRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        cogmate = self.get_cogmate(agent_id)
        ns = cogmate.namespace
        days_ahead = config.get('days_ahead', 30)

        expired = self._get_expired(ns)
        expiring = self._get_expiring(ns, days_ahead)

        if not expired and not expiring:
            return None  # Signal: don't create insight

        today = datetime.now().strftime("%Y-%m-%d")
        content = self._format(expired, expiring, days_ahead)

        return {
            "title": f"📅 时效审查 · {today}",
            "content": content,
            "summary": f"{len(expired)} 条已过期, {len(expiring)} 条即将过期",
            "metadata": {
                "expired_count": len(expired),
                "expiring_count": len(expiring),
                "days_ahead": days_ahead,
            }
        }

    def _get_expired(self, namespace: str) -> List[Dict]:
        from cogmate_core.config import get_sqlite
        conn = get_sqlite()
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT fact_id, summary, content_type, valid_until, created_at
            FROM facts
            WHERE namespace = ? AND valid_until IS NOT NULL AND valid_until < ?
            ORDER BY valid_until DESC
        """, (namespace, today))
        results = [
            {'fact_id': r[0], 'summary': r[1], 'content_type': r[2], 'valid_until': r[3], 'created_at': r[4]}
            for r in cursor.fetchall()
        ]
        conn.close()
        return results

    def _get_expiring(self, namespace: str, days_ahead: int) -> List[Dict]:
        from cogmate_core.config import get_sqlite
        conn = get_sqlite()
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        future = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT fact_id, summary, content_type, valid_until, created_at
            FROM facts
            WHERE namespace = ? AND valid_until IS NOT NULL AND valid_until >= ? AND valid_until <= ?
            ORDER BY valid_until ASC
        """, (namespace, today, future))
        results = [
            {'fact_id': r[0], 'summary': r[1], 'content_type': r[2], 'valid_until': r[3], 'created_at': r[4]}
            for r in cursor.fetchall()
        ]
        conn.close()
        return results

    def _format(self, expired, expiring, days_ahead):
        lines = ["# 📅 时效审查报告\n"]

        if expired:
            lines.append(f"## ⚠️ 已过期: {len(expired)} 条\n")
            for f in expired[:10]:
                lines.append(f"- `{f['fact_id'][:8]}` **[{f['valid_until']}]** {f['summary'][:50]}...")
            if len(expired) > 10:
                lines.append(f"\n... 还有 {len(expired) - 10} 条")
            lines.append("")

        if expiring:
            lines.append(f"## 🔔 {days_ahead}天内过期: {len(expiring)} 条\n")
            for f in expiring[:10]:
                lines.append(f"- `{f['fact_id'][:8]}` **[{f['valid_until']}]** {f['summary'][:50]}...")
            if len(expiring) > 10:
                lines.append(f"\n... 还有 {len(expiring) - 10} 条")

        return "\n".join(lines)
