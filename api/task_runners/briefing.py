"""
Briefing Runner — 资讯简报
三段式：关注领域深挖 → 相关领域拓展 → 跨界启发
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
        print(f"[Briefing] Search error: {e}")
    return []


async def call_llm_async(llm_config: dict, prompt: str, max_tokens: int = 2000, retries: int = 3) -> str:
    """调用 agent 配置的 LLM（带重试 + 429 退避）"""
    import asyncio

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

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                if provider == 'anthropic':
                    resp = await client.post(url, headers={
                        "x-api-key": api_key, "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    }, json={"model": model or "claude-3-haiku-20240307", "max_tokens": max_tokens,
                             "messages": [{"role": "user", "content": prompt}]})
                else:
                    resp = await client.post(url, headers={
                        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"
                    }, json={"model": model, "max_tokens": max_tokens,
                             "messages": [{"role": "user", "content": prompt}]})

                if resp.status_code == 429:
                    wait = min(5 * (attempt + 1), 20)
                    print(f"[LLM] 429 rate limited, waiting {wait}s (attempt {attempt+1}/{retries})")
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()

                if provider == 'anthropic':
                    return resp.json().get('content', [{}])[0].get('text', '')
                else:
                    choices = resp.json().get('choices', [])
                    return choices[0].get('message', {}).get('content', '') if choices else ''

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < retries - 1:
                wait = min(5 * (attempt + 1), 20)
                print(f"[LLM] 429 rate limited, waiting {wait}s (attempt {attempt+1}/{retries})")
                await asyncio.sleep(wait)
                continue
            print(f"[LLM] HTTP error: {e}")
            return ""
        except Exception as e:
            print(f"[LLM] error: {e}")
            return ""

    print(f"[LLM] exhausted {retries} retries")
    return ""


class BriefingRunner(BaseTaskRunner):

    async def run(self, agent_id: str, config: dict) -> dict:
        import asyncio
        llm_config = self.get_llm_config(agent_id)
        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 确定关注领域 + 拓展领域（一次 LLM 调用搞定两件事）
        focus = config.get('focus_domains', [])
        related_domains = []
        if not focus:
            focus, related_domains = await self._extract_interests_and_related(agent_id, llm_config)
        if not focus:
            focus = ['AI Agent生态', '金融市场与宏观经济', '地缘政治与军事冲突']

        # 2. 搜索阶段（并行，不调 LLM）
        # 2a. 关注领域深挖
        focus_results = {}
        for domain in focus[:4]:
            results = []
            for q in [f"{domain} 最新重大进展 {today[:7]}", f"{domain} 深度分析 本周"]:
                results.extend(await brave_search(q, count=4))
            seen = set()
            unique = [r for r in results if r['url'] not in seen and not seen.add(r['url'])]
            focus_results[domain] = unique[:6]

        # 2b. 拓展领域
        related_results = {}
        extra_count = config.get('extra_domains', 2)
        if not related_domains and extra_count > 0:
            # Fallback: hardcoded related if LLM didn't provide
            related_domains = self._fallback_related(focus)
        for domain in related_domains[:extra_count]:
            results = await brave_search(f"{domain} 最新进展 重要动态", count=4)
            if results:
                related_results[domain] = results

        # 2c. 跨界
        wildcard_results = []
        if config.get('wildcard', True):
            for q in ["unexpected scientific breakthrough 2026", "跨学科 颠覆性 发现 2026"]:
                wildcard_results.extend(await brave_search(q, count=3))
            wildcard_results = wildcard_results[:5]

        # 3. 一次 LLM 调用：筛选 + 撰写完整简报
        content = await self._compose_briefing(
            llm_config, today, focus, focus_results, related_results, wildcard_results
        )

        total = sum(len(v) for v in focus_results.values()) + sum(len(v) for v in related_results.values()) + len(wildcard_results)
        return {
            "title": f"🌅 资讯简报 · {today}",
            "content": content,
            "summary": f"{len(focus)} 个关注领域, {len(related_results)} 个拓展领域, {total} 条原始资讯",
            "metadata": {
                "focus_domains": focus,
                "related_domains": list(related_results.keys()),
                "sources_count": total,
            }
        }

    async def _extract_interests_and_related(self, agent_id: str, llm_config: dict) -> tuple:
        """一次 LLM 调用：从知识库提取关注领域 + 推荐拓展领域"""
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
                return [], []

            recent = "\n".join([f"[{r[1]}] {r[0][:80]}" for r in rows])

            prompt = f"""基于以下用户最近的知识库记录，完成两个任务。

任务1：提取 3-4 个用户持续关注的领域（适合作为新闻搜索关键词，要具体，如"AI Agent生态"而不是"科技"）
任务2：推荐 2-3 个与用户兴趣相关但用户可能没直接接触的领域（打破信息茧房，要有意外感）

用户最近记录：
{recent}

严格按以下格式输出，不要加编号和解释：
FOCUS:
领域1
领域2
领域3
RELATED:
领域1
领域2"""

            result = await call_llm_async(llm_config, prompt, max_tokens=300)
            if not result:
                return [], []

            focus, related = [], []
            section = None
            for line in result.strip().split('\n'):
                line = line.strip().strip('-').strip('·').strip()
                if 'FOCUS' in line.upper():
                    section = 'focus'
                    continue
                elif 'RELATED' in line.upper():
                    section = 'related'
                    continue
                if not line or len(line) < 2 or len(line) > 25:
                    continue
                if section == 'focus':
                    focus.append(line)
                elif section == 'related':
                    related.append(line)

            print(f"[Briefing] Extracted focus={focus}, related={related}")
            return focus[:5], related[:3]
        except Exception as e:
            print(f"[Briefing] Extract interests error: {e}")
            return [], []

    def _fallback_related(self, focus: list) -> list:
        """无 LLM 时的关联领域 fallback"""
        domain_map = {
            'AI': ['合成生物学', '脑机接口'],
            '金融': ['央行数字货币', '气候金融'],
            '地缘': ['稀土供应链', '太空竞赛'],
            '制造': ['仿生材料', '微型核反应堆'],
            '能源': ['核聚变进展', '深海采矿'],
        }
        related = []
        for f in focus:
            for key, vals in domain_map.items():
                if key in f:
                    related.extend(vals)
        return related[:3] if related else ['合成生物学', '太空经济']

    async def _compose_briefing(self, llm_config, today, focus, focus_results, related_results, wildcard_results):
        """LLM 筛选 + 撰写三段式简报"""

        # 构建原始素材
        raw_sections = []

        raw_sections.append("## 第一层：用户关注领域的搜索结果")
        for domain, items in focus_results.items():
            raw_sections.append(f"\n### {domain}")
            for item in items:
                raw_sections.append(f"- {item['title']}: {item['description']}")
                raw_sections.append(f"  URL: {item['url']}")

        if related_results:
            raw_sections.append("\n## 第二层：相关拓展领域的搜索结果")
            for domain, items in related_results.items():
                raw_sections.append(f"\n### {domain}")
                for item in items:
                    raw_sections.append(f"- {item['title']}: {item['description']}")
                    raw_sections.append(f"  URL: {item['url']}")

        if wildcard_results:
            raw_sections.append("\n## 第三层：跨界搜索结果")
            for item in wildcard_results:
                raw_sections.append(f"- {item['title']}: {item['description']}")
                raw_sections.append(f"  URL: {item['url']}")

        raw = "\n".join(raw_sections)

        prompt = f"""你是一位高水平的资讯编辑，为用户编写每日早间简报。

日期：{today}
用户关注领域：{', '.join(focus)}

以下是搜索到的原始资讯素材（分三层）。请你：

1. **筛选**：从每层中挑出真正有价值的信息，过滤掉广告、基金公告、无关内容
2. **深挖**：对关注领域的重要事件，不只是标题，要点出核心信息、影响、和用户已有知识的关联
3. **撰写**：按以下三段式结构输出

## 输出格式要求（严格遵守）

```
───

🌅 资讯简报 | {today}

───

📍 你关注的

1. [领域标签] 标题

• 核心信息点1
• 核心信息点2
• 与用户已有关注点的关联（如有）

2. [领域标签] 标题
...

───

🔭 你可能感兴趣

N. [领域标签] 标题

• 核心信息
• 为什么跟你有关：解释与用户关注领域的关联

───

💡 跨界启发

N. [领域标签] 标题

• 核心内容
• 启发：用一句话点出对其他领域的启发意义
```

规则：
- 📍 部分：3-4 条，每条要有深度，不是简单复述标题
- 🔭 部分：1-2 条，要解释为什么跟用户有关
- 💡 部分：1 条，要有意外感和启发性
- 每条附来源链接
- 如果某个搜索结果质量太低（广告、PDF、基金公告等），直接跳过
- 使用 Markdown 格式

原始素材：
{raw[:6000]}

直接输出简报："""

        content = await call_llm_async(llm_config, prompt, max_tokens=2000)

        if not content:
            # Fallback without LLM
            lines = [f"# 🌅 资讯简报 · {today}\n"]
            lines.append("## 📍 关注领域\n")
            for domain, items in focus_results.items():
                lines.append(f"### {domain}")
                for item in items[:2]:
                    lines.append(f"- **{item['title']}**")
                    lines.append(f"  {item['description'][:100]}")
                    lines.append(f"  [来源]({item['url']})")
                lines.append("")
            if related_results:
                lines.append("## 🔭 拓展领域\n")
                for domain, items in related_results.items():
                    lines.append(f"### {domain}")
                    for item in items[:2]:
                        lines.append(f"- **{item['title']}** — {item['description'][:80]}")
                    lines.append("")
            if wildcard_results:
                lines.append("## 💡 跨界\n")
                for item in wildcard_results[:1]:
                    lines.append(f"- **{item['title']}** — {item['description'][:100]}")
            content = "\n".join(lines)

        return content
