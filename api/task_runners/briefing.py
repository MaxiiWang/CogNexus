"""
Briefing Runner — 资讯简报
搜索用户关注领域的最新资讯，LLM 撰写简报
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
        # Try reading from OpenClaw auth profiles
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
                results = []
                for r in data.get('web', {}).get('results', [])[:count]:
                    results.append({
                        'title': r.get('title', ''),
                        'description': r.get('description', ''),
                        'url': r.get('url', ''),
                    })
                return results
    except Exception as e:
        print(f"[Briefing] Brave search error: {e}")
    return []


async def call_llm_async(llm_config: dict, prompt: str, max_tokens: int = 1500) -> str:
    """调用 agent 配置的 LLM"""
    provider = (llm_config.get('provider') or '').lower()
    api_key = llm_config.get('api_key', '')
    model = llm_config.get('model', '')
    base_url = llm_config.get('base_url') or llm_config.get('endpoint', '')

    if not api_key:
        return ""

    # Build URL
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

    # Ensure URL has endpoint path
    url = base_url.rstrip('/')
    if '/chat/completions' not in url and '/messages' not in url:
        if provider == 'anthropic':
            url += '/messages'
        else:
            url += '/chat/completions'

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
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
                data = resp.json()
                return data.get('content', [{}])[0].get('text', '')
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
                data = resp.json()
                choices = data.get('choices', [])
                return choices[0].get('message', {}).get('content', '') if choices else ''
    except Exception as e:
        print(f"[Briefing] LLM call error: {e}")
        return ""


class BriefingRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        llm_config = self.get_llm_config(agent_id)
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 确定关注领域
        focus = config.get('focus_domains', [])
        if not focus:
            focus = self._extract_focus_from_kb(agent_id)
        if not focus:
            focus = ['科技', '财经', 'AI']

        # 2. 搜索各领域
        all_results = {}
        for domain in focus[:5]:
            results = await brave_search(f"{domain} 最新动态 {today[:7]}", count=4)
            if results:
                all_results[domain] = results

        # 额外关联领域
        extra_count = config.get('extra_domains', 2)
        if extra_count > 0:
            related = self._get_related_domains(focus)
            for domain in related[:extra_count]:
                results = await brave_search(f"{domain} 最新", count=3)
                if results:
                    all_results[f"📎 {domain}"] = results

        # 跨界启发
        if config.get('wildcard', True):
            wild = await brave_search("unexpected breakthrough innovation technology 2026", count=2)
            if wild:
                all_results["🌍 跨界"] = wild

        # 3. LLM 撰写
        content = await self._compose(llm_config, focus, all_results, today)

        total_sources = sum(len(v) for v in all_results.values())
        return {
            "title": f"📰 资讯简报 · {today}",
            "content": content,
            "summary": f"{len(focus)} 个领域 {total_sources} 条资讯",
            "metadata": {
                "domains": focus,
                "sources_count": total_sources,
                "domains_searched": list(all_results.keys()),
            }
        }

    def _extract_focus_from_kb(self, agent_id: str) -> list:
        """从知识库提取关注领域（高频 context 关键词）"""
        try:
            cogmate = self.get_cogmate(agent_id)
            from cogmate_core.config import get_sqlite
            conn = get_sqlite()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT context FROM facts
                WHERE namespace = ? AND context IS NOT NULL
                ORDER BY created_at DESC LIMIT 50
            """, (cogmate.namespace,))
            contexts = [row[0] for row in cursor.fetchall() if row[0]]
            conn.close()

            if not contexts:
                return []

            # Simple keyword extraction
            words = Counter()
            for ctx in contexts:
                for w in ctx.replace('，', ' ').replace('、', ' ').split():
                    if len(w) >= 2:
                        words[w] += 1
            return [w for w, _ in words.most_common(5)]
        except Exception:
            return []

    def _get_related_domains(self, focus: list) -> list:
        """基于关注领域推荐关联领域"""
        domain_map = {
            'AI': ['芯片', '机器人', '自动驾驶'],
            '科技': ['AI', '量子计算', '生物技术'],
            '金融': ['宏观经济', '加密货币', '房地产'],
            '财经': ['股市', '基金', '税务'],
            '制造': ['工业4.0', '新材料', '3D打印'],
            '能源': ['新能源', '储能', '碳交易'],
        }
        related = []
        focus_set = set(focus)
        for f in focus:
            for r in domain_map.get(f, []):
                if r not in focus_set and r not in related:
                    related.append(r)
        return related

    async def _compose(self, llm_config: dict, focus: list, results: dict, today: str) -> str:
        """用 LLM 撰写简报"""
        # Build raw material
        lines = []
        for domain, items in results.items():
            lines.append(f"\n## {domain}")
            for item in items:
                lines.append(f"- **{item['title']}**: {item['description'][:150]}")
                lines.append(f"  来源: {item['url']}")

        raw = "\n".join(lines)

        prompt = f"""你是一位专业的资讯编辑。请基于以下搜索结果，为用户撰写一份简洁的每日资讯简报。

日期: {today}
用户关注领域: {', '.join(focus)}

原始搜索结果:
{raw}

要求:
1. 按领域分组，每个领域 1-3 条最有价值的资讯
2. 每条用一句话总结核心信息，附来源链接
3. 如果有跨领域关联，在最后点出
4. 语言简洁，避免废话
5. 使用 Markdown 格式

直接输出简报内容："""

        content = await call_llm_async(llm_config, prompt, max_tokens=1500)

        if not content:
            # Fallback: structured output without LLM
            fallback = [f"# 📰 资讯简报 · {today}\n"]
            for domain, items in results.items():
                fallback.append(f"## {domain}")
                for item in items:
                    fallback.append(f"- **{item['title']}**")
                    fallback.append(f"  {item['description'][:120]}")
                    fallback.append(f"  [来源]({item['url']})")
                fallback.append("")
            content = "\n".join(fallback)

        return content
