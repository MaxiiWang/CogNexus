"""
Base Task Runner
"""
import json
from typing import Dict, Optional
from database import get_db


class BaseTaskRunner:
    """任务执行器基类"""

    def get_cogmate(self, agent_id: str):
        """获取 agent 对应的 CogmateAgent 实例"""
        from cogmate_core import CogmateAgent
        ns = self._get_namespace(agent_id)
        return CogmateAgent(namespace=ns)

    def get_llm_config(self, agent_id: str) -> dict:
        """获取 agent 的 LLM 配置"""
        conn = get_db()
        row = conn.execute(
            "SELECT llm_config FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        conn.close()
        if row and row['llm_config']:
            return json.loads(row['llm_config'])
        return {}

    def _get_namespace(self, agent_id: str) -> str:
        conn = get_db()
        row = conn.execute(
            "SELECT namespace FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        conn.close()
        return row['namespace'] if row else 'default'

    async def run(self, agent_id: str, config: dict) -> dict:
        """
        执行任务。子类必须实现。

        Returns:
            {
                "title": str,        # 报告标题
                "content": str,      # Markdown 正文
                "summary": str,      # 一句话摘要
                "metadata": dict     # 结构化数据（前端用）
            }
        """
        raise NotImplementedError
