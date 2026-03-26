"""
Briefing Runner — 资讯简报
两阶段: LLM 提取关注领域 + 搜索 → LLM 筛选撰写三段式简报
"""
import json
import httpx
import os
from datetime import datetime
from collections import Counter
from typing import List, Dict
from .base import BaseTaskRunner


async def brave_search(query: str, count: int = 5) -> List[Dict]:
    """Brave Search API"""
    api_key = os.environ.get('BRAVE_API_KEY', '')
    if not api_key:
        from pathlib import Path
        auth_file = Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
        if auth_file.exists():
            try:
                data = json.loads(auth_file.read_text())
                for p in data.get('profiles', []):
                    if p.get('id') == 'brave':
                        api_key = p.get('key', '')
                        break
            except Exception:
                pass
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count, "search_lang": "zh-hans"},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        'title': r.get('title', ''),
                        'description': r.get('description', '')[:200],
                        'url': r.get('url', ''),
                    }
                    for r in data.get('web', {}).get('results', [])[:count]
                ]
    except Exception as e:
        print(f"[Briefing] Brave search error: {e}")
    return []


async def call_llm_async(llm_config: dict, prompt: str, max_tokens: int = 2000) -> str:
    """调用 agent 配置的 LLM"""
    provider = (llm_config.get('provider') or '').lower()
    api_key = llm_config.get('api_key', '')
    model = llm_config.get('model', '')
    base_url = llm_config.get('base_url') or llm_config.get('endpoint', '')

    if not api_key:
        return ""

    if not base_url:
        url_map = {
            'anthropic': 'https://api.anthropic.com/v1/messages',
            'openai': 'https://api.openai.com/v1/chat/completions',
            'deepseek': 'https://api.deepseek.com/v1/chat/completions',
            'doubao': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
        }
        base_url = url_map.get(provider, '')

    if not base_url:
        return ""

    url = base_url.rstrip('/')
    if '/chat/completions' not in url and '/messages' not in url:
        url += '/messages' if provider == 'anthropic' else '/chat/completions'

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            if provider == 'anthropic':
                resp = await client.post(url, headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }, json={
                    "model": model or "claude-3-haiku-20240307",
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}]
                })
                resp.raise_for_status()
                return resp.json().get('content', [{}])[0].get('text', '')
            else:
                resp = await client.post(url, headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }, json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}]
                })
                resp.raise_for_status()
                choices = resp.json().get('choices', [])
                return choices[0].get('message', {}).get('content', '') if choices else ''
    except Exception as e:
        print(f"[Briefing] LLM call error: {e}")
        return ""


class BriefingRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        llm_config = self.get_llm_config(agent_id)
        today = datetime.now().strftime("%Y-%m-%d")

        # === Phase 1: 确定搜索词 ===
        user_domains = config.get('focus_domains', [])
        if not user_domains:
            user_domains = await self._extract_domains_via_llm(agent_id, llm_config)
        if not user_domains:
            user_domains = ['AI', '科技', '金融']

        # === Phase 2: 搜索 ===
        # 关注领域: 深挖 (每个领域多条)
        focus_results = {}
        for domain in user_domains[:4]:
            r = await brave_search(f"{domain} 最新重要进展 {today[:7]}", count=5)
            if r:
                focus_results[domain] = r

        # 关联领域: 打破信息茧房
        related_domains = await self._get_related_via_llm(user_domains, llm_config)
        related_results = {}
        for domain in related_domains[:3]:
            r = await brave_search(f"{domain} 重要动态 {today[:7]}", count=4)
            if r:
                related_results[domain] = r

        # 跨界启发
        wildcard_results = []
        if config.get('wildcard', True):
            wildcard_results = await brave_search(
                "surprising scientific breakthrough OR unexpected technology innovation 2026",
                count=4
            )

        # === Phase 3: LLM 筛选 + 撰写 ===
        content = await self._compose_briefing(
            llm_config, today, user_domains,
            focus_results, related_results, wildcard_results
        )

        total = sum(len(v) for v in focus_results.values()) + sum(len(v) for v in related_results.values()) + len(wildcard_results)
        return {
            "title": f"🌅 资讯简报 · {today}",
            "content": content,
            "summary": f"{len(user_domains)} 个关注领域, {len(related_domains)} 个关联领域, {total} 条原始资讯",
            "metadata": {
                "focus_domains": user_domains,
                "related_domains": related_domains,
                "sources_count": total,
            }
        }

    async def _extract_domains_via_llm(self, agent_id: str, llm_config: dict) -> list:
        """用 LLM 从知识库摘要中提取用户真正关注的领域"""
        # 拿最近的知识条目
        try:
            cogmate = self.get_cogmate(agent_id)
            from cogmate_core.config import get_sqlite
            conn = get_sqlite()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT summary, content_type FROM facts
                WHERE namespace = ?
                ORDER BY created_at DESC LIMIT 30
            """, (cogmate.namespace,))
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                return []
            kb_sample = "\n".join([f"[{r[1]}] {r[0]}" for r in rows])
        except Exception:
            return []

        prompt = f"""分析以下用户知识库的最近记录，提取他真正关注的 3-5 个领域。

要求：
- 输出具体的领域名称，适合作为新闻搜索关键词
- 不要输出太宽泛的（如"科技"），要具体（如"AI Agent"、"增材制造"、"地缘政治"）
- 也不要太窄（如某个具体产品名）
- 只输出 JSON 数组，不要其他内容

知识库样本:
{kb_sample[:2000]}

输出格式: ["领域1", "领域2", "领域3"]"""

        result = await call_llm_async(llm_config, prompt, max_tokens=200)
        try:
            domains = json.loads(result.strip())
            if isinstance(domains, list):
                return [str(d) for d in domains[:5]]
        except Exception:
            pass
        return []

    async def _get_related_via_llm(self, focus: list, llm_config: dict) -> list:
        """让 LLM 推荐关联但不同的领域，用于打破信息茧房"""
        if not llm_config.get('api_key'):
            # Fallback
            return ['机器人', '认知科学', '新能源']

        prompt = f"""用户关注以下领域: {', '.join(focus)}

请推荐 3 个与这些领域有关联但用户可能不会主动关注的领域，目的是打破信息茧房。

要求：
- 不能是用户已关注领域的子集或同义词
- 要有跨学科的启发性（如关注AI的人可能受益于认知科学，关注制造的人可能受益于材料学）
- 每个领域要适合作为新闻搜索关键词
- 只输出 JSON 数组

输出: ["领域1", "领域2", "领域3"]"""

        result = await call_llm_async(llm_config, prompt, max_tokens=150)
        try:
            domains = json.loads(result.strip())
            if isinstance(domains, list):
                return [str(d) for d in domains[:3]]
        except Exception:
            pass
        return ['认知科学', '新材料', '复杂系统']

    async def _compose_briefing(self, llm_config, today, focus_domains,
                                 focus_results, related_results, wildcard_results):
        """LLM 筛选 + 撰写三段式简报"""
        # 构建原始素材
        raw_parts = []

        raw_parts.append("=== 关注领域搜索结果 ===")
        for domain, items in focus_results.items():
            raw_parts.append(f"\n[{domain}]")
            for item in items:
                raw_parts.append(f"- {item['title']}: {item['description']}")

        raw_parts.append("\n=== 关联领域搜索结果 ===")
        for domain, items in related_results.items():
            raw_parts.append(f"\n[{domain}]")
            for item in items:
                raw_parts.append(f"- {item['title']}: {item['description']}")

        if wildcard_results:
            raw_parts.append("\n=== 跨界搜索结果 ===")
            for item in wildcard_results:
                raw_parts.append(f"- {item['title']}: {item['description']}")

        raw = "\n".join(raw_parts)

        prompt = f"""你是一位顶级资讯编辑。请基于以下搜索结果，为用户撰写每日资讯简报。

日期: {today}
用户关注领域: {', '.join(focus_domains)}

原始搜索结果:
{raw[:4000]}

请严格按以下三段式结构撰写：

## 📍 你关注的
从「关注领域」结果中，筛选 3-4 条最有价值、最重要的资讯。
- 每条：一个醒目的标题 + 3-4 个要点（bullet point），要点要有信息量
- 深挖关键细节，不要只是复述标题
- 如果能发现不同新闻之间的关联，请点出

## 🔭 你可能感兴趣
从「关联领域」结果中，筛选 2 条与用户关注领域有隐含关联的资讯。
- 明确说明"为什么这和你有关"（用一句话点出跨领域关联）

## 💡 跨界启发
从所有结果中（优先跨界搜索），挑 1 条最能启发思考的。
- 用一句「启发」总结其对用户的潜在价值

规则:
- 过滤掉明显无关的内容（基金产品概要、招聘广告等）
- 过滤掉内容太浅的（纯标题无实质信息的）
- 使用 Markdown 格式
- 语言简洁有力，不要废话
- 不需要加来源链接

直接输出简报："""

        content = await call_llm_async(llm_config, prompt, max_tokens=2000)

        if not content:
            # Fallback: 无 LLM 时的结构化输出
            lines = [f"# 🌅 资讯简报 · {today}\n"]
            lines.append("## 📍 你关注的")
            for domain, items in focus_results.items():
                for item in items[:2]:
                    lines.append(f"- **[{domain}]** {item['title']}")
            lines.append("\n## 🔭 你可能感兴趣")
            for domain, items in related_results.items():
                for item in items[:1]:
                    lines.append(f"- **[{domain}]** {item['title']}")
            if wildcard_results:
                lines.append("\n## 💡 跨界启发")
                lines.append(f"- {wildcard_results[0]['title']}")
            content = "\n".join(lines)

        return content
