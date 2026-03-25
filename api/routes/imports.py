"""
Knowledge Import API
Prefix: /api/knowledge/{namespace}/imports

Handles file upload, parsing, chunking, and LLM-powered knowledge extraction
from external documents (.md, .txt, .csv, .pdf, .zip).
"""
import json
import os
import re
import csv
import io
import uuid
import zipfile
import asyncio
import concurrent.futures
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Body

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from database import get_db
from routes.knowledge import verify_namespace, _require_owner, _get_agent_llm_config

router = APIRouter(prefix="/api/knowledge/{namespace}/imports", tags=["knowledge-imports"])

# Upload storage directory
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "imports"

# ==================== Parsers ====================

def _extract_front_matter(content: str) -> tuple:
    """Extract YAML front matter from markdown. Returns (front_matter_dict, remaining_content)."""
    if not content.startswith('---'):
        return {}, content
    try:
        end = content.index('---', 3)
        fm_raw = content[3:end].strip()
        remaining = content[end + 3:].strip()
        # Simple YAML parse (key: value pairs)
        fm = {}
        for line in fm_raw.split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                key = key.strip().lower()
                val = val.strip().strip('"').strip("'")
                if val.startswith('[') and val.endswith(']'):
                    val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',')]
                fm[key] = val
        return fm, remaining
    except (ValueError, Exception):
        return {}, content


def _extract_wikilinks(content: str) -> list:
    """Extract [[wikilinks]] and [[page|alias]] from markdown. Returns list of linked page names."""
    links = []
    for m in re.finditer(r'\[\[([^\]]+)\]\]', content):
        link_text = m.group(1)
        # Handle [[page|alias]] format
        page_name = link_text.split('|')[0].strip()
        # Remove heading anchors like [[page#heading]]
        page_name = page_name.split('#')[0].strip()
        if page_name:
            links.append(page_name)
    return links


def _clean_obsidian_markdown(content: str) -> str:
    """Convert Obsidian-specific markdown to readable text."""
    # Convert ![[embed]] FIRST (before wikilink stripping)
    content = re.sub(r'!\[\[([^\]]+)\]\]', r'（引用: \1）', content)
    # Convert [[page|alias]] to alias, [[page]] to page
    content = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', content)
    content = re.sub(r'\[\[([^\]]+)\]\]', r'\1', content)
    # Convert Obsidian callouts > [!note] to text
    content = re.sub(r'>\s*\[!(\w+)\]\s*', r'[\1] ', content)
    # Strip #tags but keep the tag name for context
    content = re.sub(r'(?<!\w)#([a-zA-Z\u4e00-\u9fff][\w/\u4e00-\u9fff-]*)', r'(标签:\1)', content)
    return content


def parse_markdown(content: str, obsidian_mode: bool = False) -> list:
    """Split markdown by ## headings into chunks. Optionally handle Obsidian syntax."""
    # Extract front matter
    front_matter, content = _extract_front_matter(content)
    fm_context = ''
    if front_matter:
        tags = front_matter.get('tags', [])
        if isinstance(tags, str):
            tags = [tags]
        if tags:
            fm_context = '标签: ' + ', '.join(tags) + '\n'
        title = front_matter.get('title', '')
        if title:
            fm_context = f'标题: {title}\n' + fm_context

    # Clean Obsidian syntax if needed
    if obsidian_mode:
        content = _clean_obsidian_markdown(content)

    sections = re.split(r'\n(?=##\s)', content)
    if len(sections) <= 1:
        # No headings — split by paragraphs
        sections = re.split(r'\n\n+', content)

    chunks = []
    for s in sections:
        s = s.strip()
        if not s:
            continue
        if len(s) < 50 and chunks:
            chunks[-1] += '\n' + s
        elif len(s) > 3000:
            # Split very long sections at sentence boundaries
            sentences = re.split(r'(?<=[。！？.!?\n])\s*', s)
            buf = ''
            for sent in sentences:
                if len(buf) + len(sent) > 1500 and buf:
                    chunks.append(buf.strip())
                    buf = sent
                else:
                    buf += sent
            if buf.strip():
                chunks.append(buf.strip())
        else:
            chunks.append(s)

    # Prepend front matter context to first chunk
    if fm_context and chunks:
        chunks[0] = fm_context + chunks[0]

    return [c for c in chunks if len(c.strip()) >= 10]


def parse_text(content: str) -> list:
    """Split plain text by paragraphs."""
    paragraphs = re.split(r'\n\n+', content)
    chunks = []
    buf = ''
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) < 1500:
            buf += ('\n\n' if buf else '') + p
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c.strip()) >= 10]


def parse_csv_content(content: str) -> list:
    """Parse CSV rows into chunks."""
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if len(rows) < 2:
        return []
    headers = rows[0]
    chunks = []
    for row in rows[1:]:
        parts = [f"{headers[i]}: {row[i]}" for i in range(min(len(headers), len(row))) if row[i].strip()]
        if parts:
            chunks.append(', '.join(parts))
    return chunks


def parse_pdf(file_path: str) -> list:
    """Parse PDF using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("PDF 解析需要 pdfplumber 库，请联系管理员安装: pip install pdfplumber")

    chunks = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                chunks.extend(parse_text(text))
    return chunks


def _parse_canvas_file(raw_json: str, all_md_files: dict = None) -> tuple:
    """Parse JSON Canvas (.canvas) file.
    Returns (text_content, relationships) where relationships is list of (from_node, to_node, label)."""
    try:
        canvas = json.loads(raw_json)
    except Exception:
        return '', []

    nodes = canvas.get('nodes', [])
    edges = canvas.get('edges', [])
    node_map = {}  # id -> label/text
    texts = []
    relationships = []

    for node in nodes:
        nid = node.get('id', '')
        ntype = node.get('type', '')

        if ntype == 'text':
            text = node.get('text', '').strip()
            if text:
                texts.append(text)
                node_map[nid] = text[:60]

        elif ntype == 'file':
            file_path = node.get('file', '')
            node_map[nid] = file_path
            # If we have the referenced md file content, include it
            if all_md_files and file_path in all_md_files:
                texts.append(f"[引用: {file_path}]\n{all_md_files[file_path][:500]}")

        elif ntype == 'link':
            url = node.get('url', '')
            if url:
                texts.append(f"链接: {url}")
                node_map[nid] = url

        elif ntype == 'group':
            label = node.get('label', '')
            if label:
                node_map[nid] = label

    # Extract edge relationships
    for edge in edges:
        from_id = edge.get('fromNode', '')
        to_id = edge.get('toNode', '')
        label = edge.get('label', '')
        from_name = node_map.get(from_id, from_id)
        to_name = node_map.get(to_id, to_id)
        if from_name and to_name:
            relationships.append((from_name, to_name, label))

    combined = '\n\n'.join(texts)
    if relationships:
        rel_text = '\n关系:\n' + '\n'.join(
            f'- {r[0][:40]} → {r[1][:40]}' + (f' ({r[2]})' if r[2] else '')
            for r in relationships[:20]
        )
        combined += rel_text

    return combined, relationships


def parse_zip(file_path: str) -> tuple:
    """Parse zip file, detect type (Notion/Obsidian/archive), extract text files."""
    detected_type = 'archive'
    files = []

    with zipfile.ZipFile(file_path, 'r') as zf:
        names = zf.namelist()

        # Skip directories and hidden files
        skip_prefixes = ('.obsidian/', '.trash/', '.git/', '__MACOSX/')

        # Detect type
        has_obsidian = any('.obsidian/' in n for n in names)
        has_canvas = any(n.endswith('.canvas') for n in names)
        has_notion_uuids = any(re.search(r'[a-f0-9]{32}', n) for n in names if n.endswith('.md'))

        if has_obsidian or has_canvas:
            detected_type = 'obsidian'
        elif has_notion_uuids:
            detected_type = 'notion_export'

        # For Obsidian: first pass to collect all md files for canvas cross-referencing
        all_md_files = {}
        if detected_type == 'obsidian':
            for name in names:
                if any(name.startswith(p) or ('/' + p) in name for p in skip_prefixes):
                    continue
                if name.lower().endswith('.md'):
                    try:
                        content = zf.read(name).decode('utf-8', errors='replace')
                        # Use relative filename without extension as key
                        key = name.rsplit('.', 1)[0]
                        # Also store with just the filename (no path) for wikilink matching
                        basename = key.split('/')[-1]
                        all_md_files[name] = content
                        all_md_files[key] = content
                        all_md_files[basename] = content
                    except Exception:
                        pass

        for name in names:
            # Skip hidden/config directories
            if any(name.startswith(p) or ('/' + p) in name for p in skip_prefixes):
                continue
            if name.endswith('/'):
                continue

            lower = name.lower()

            if lower.endswith('.md'):
                try:
                    content = zf.read(name).decode('utf-8', errors='replace')
                    if content.strip():
                        # For Obsidian, add folder path as context
                        if detected_type == 'obsidian' and '/' in name:
                            folder_path = '/'.join(name.split('/')[:-1])
                            content = f"[路径: {folder_path}]\n\n{content}"
                        files.append((name, content))
                except Exception:
                    pass

            elif lower.endswith(('.txt', '.csv')):
                try:
                    content = zf.read(name).decode('utf-8', errors='replace')
                    if content.strip():
                        files.append((name, content))
                except Exception:
                    pass

            elif lower.endswith('.canvas'):
                try:
                    raw = zf.read(name).decode('utf-8', errors='replace')
                    canvas_text, _ = _parse_canvas_file(raw, all_md_files)
                    if canvas_text.strip():
                        files.append((name, f"[Canvas: {name}]\n\n{canvas_text}"))
                except Exception:
                    pass

    return detected_type, files


# ==================== Notion API Helpers ====================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_search_pages(token: str) -> list:
    """Search all pages accessible to this integration token."""
    import httpx
    pages = []
    start_cursor = None
    while True:
        body = {"filter": {"value": "page", "property": "object"}, "page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = httpx.post(
            f"{NOTION_API_BASE}/search",
            headers=_notion_headers(token),
            json=body,
            timeout=15.0,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        for page in data.get("results", []):
            title = ""
            props = page.get("properties", {})
            for prop in props.values():
                if prop.get("type") == "title":
                    title_parts = prop.get("title", [])
                    title = "".join(t.get("plain_text", "") for t in title_parts)
                    break
            if not title:
                title = "Untitled"
            pages.append({
                "id": page["id"],
                "title": title,
                "url": page.get("url", ""),
                "last_edited": page.get("last_edited_time", ""),
                "icon": page.get("icon", {}).get("emoji", "📄") if page.get("icon") else "📄",
            })
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return pages


def notion_get_page_content(token: str, page_id: str) -> str:
    """Recursively fetch all blocks from a Notion page and convert to markdown text."""
    import httpx
    import time

    def fetch_blocks(block_id: str, depth: int = 0) -> str:
        if depth > 5:
            return ""

        texts = []
        start_cursor = None
        while True:
            url = f"{NOTION_API_BASE}/blocks/{block_id}/children?page_size=100"
            if start_cursor:
                url += f"&start_cursor={start_cursor}"

            resp = httpx.get(url, headers=_notion_headers(token), timeout=15.0)
            if resp.status_code == 429:
                time.sleep(1)
                continue
            if resp.status_code != 200:
                break

            data = resp.json()
            for block in data.get("results", []):
                text = _notion_block_to_text(block, depth)
                if text:
                    texts.append(text)

                # Recurse into children — including child_page (to get sub-page content)
                if block.get("has_children"):
                    if block["type"] == "child_page":
                        # Sub-page: add heading with title, then recurse into page content
                        title = block.get("child_page", {}).get("title", "")
                        if title:
                            texts.append(f"\n## {title}")
                        child_text = fetch_blocks(block["id"], depth + 1)
                        if child_text:
                            texts.append(child_text)
                    elif block["type"] != "child_database":
                        child_text = fetch_blocks(block["id"], depth + 1)
                        if child_text:
                            texts.append(child_text)

            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
            time.sleep(0.35)

        return "\n".join(texts)

    return fetch_blocks(page_id)


def _notion_block_to_text(block: dict, depth: int = 0) -> str:
    """Convert a single Notion block to plain/markdown text."""
    btype = block.get("type", "")
    bdata = block.get(btype, {})

    def rich_text_to_str(rich_texts: list) -> str:
        return "".join(rt.get("plain_text", "") for rt in rich_texts)

    indent = "  " * depth

    if btype == "paragraph":
        return indent + rich_text_to_str(bdata.get("rich_text", []))
    elif btype in ("heading_1", "heading_2", "heading_3"):
        level = int(btype[-1])
        return "\n" + "#" * level + " " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "bulleted_list_item":
        return indent + "- " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "numbered_list_item":
        return indent + "1. " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "to_do":
        checked = "☑" if bdata.get("checked") else "☐"
        return indent + f"{checked} " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "toggle":
        return indent + "▸ " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "code":
        lang = bdata.get("language", "")
        code = rich_text_to_str(bdata.get("rich_text", []))
        return f"\n```{lang}\n{code}\n```"
    elif btype == "quote":
        return indent + "> " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "callout":
        icon = bdata.get("icon", {}).get("emoji", "💡") if bdata.get("icon") else "💡"
        return indent + f"{icon} " + rich_text_to_str(bdata.get("rich_text", []))
    elif btype == "divider":
        return "\n---\n"
    elif btype == "table_row":
        cells = bdata.get("cells", [])
        return indent + " | ".join(rich_text_to_str(cell) for cell in cells)
    elif btype == "bookmark":
        url = bdata.get("url", "")
        caption = rich_text_to_str(bdata.get("caption", []))
        return indent + f"[{caption or url}]({url})"
    elif btype == "image":
        caption = rich_text_to_str(bdata.get("caption", []))
        return indent + f"[图片: {caption}]" if caption else ""
    elif btype in ("child_page", "child_database"):
        title = bdata.get("title", "")
        return indent + f"[子页面: {title}]"
    elif btype == "equation":
        return indent + bdata.get("expression", "")
    elif btype == "table_of_contents":
        return ""
    elif btype == "column_list":
        return ""
    elif btype == "column":
        return ""
    else:
        rt = bdata.get("rich_text", [])
        if rt:
            return indent + rich_text_to_str(rt)
        return ""


# ==================== LLM Extraction ====================

def _extract_from_chunk(chunk_text: str, source_context: str, llm_cfg: dict) -> list:
    """Extract knowledge items from a single chunk using LLM."""
    import httpx

    provider = llm_cfg.get("provider", "")
    base_url = llm_cfg.get("endpoint", "") or llm_cfg.get("base_url", "")
    if not base_url:
        provider_urls = {
            "openai": "https://api.openai.com/v1",
            "doubao": "https://ark.cn-beijing.volces.com/api/v3",
            "deepseek": "https://api.deepseek.com/v1",
            "moonshot": "https://api.moonshot.cn/v1",
        }
        base_url = provider_urls.get(provider, "https://api.openai.com/v1")

    extraction_prompt = f"""从以下内容中提取值得长期保存的知识条目。

提取原则：
- 保持知识完整性：一个完整的概念、流程、对比关系不要拆开，宁可一条长一点也不要碎片化
- 保留上下文：专业术语要带必要的解释和背景
- 合并相关内容：如果多个小点属于同一个主题（如"三种计价方法"），合并为一条
- 跳过：目录、空洞的标题、纯格式内容
- content_type: 事实（客观知识）/观点（判断分析）/决策（行动方案）/资讯（时事动态）/洞察（深层规律）

来源: {source_context}
内容:
{chunk_text[:2500]}

输出JSON数组: [{{"summary":"完整的知识内容","content_type":"类型","reason":"提取理由"}}]
无则输出[]"""

    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm_cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg.get("model", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": "你是知识提取引擎。提取完整的、有上下文的知识条目，不要碎片化。只输出JSON数组，无内容则输出[]。"},
                    {"role": "user", "content": extraction_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=45.0,
        )

        if resp.status_code != 200:
            print(f"[Import] LLM call failed: {resp.status_code}", file=sys.stderr, flush=True)
            return []

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        suggestions = json.loads(content)
        if not isinstance(suggestions, list):
            return []

        valid = []
        for item in suggestions:
            if isinstance(item, dict) and item.get("summary"):
                valid.append({
                    "summary": str(item["summary"])[:500],
                    "content_type": str(item.get("content_type", "事实"))[:10],
                    "reason": str(item.get("reason", ""))[:200],
                })
        return valid[:8]

    except Exception as e:
        print(f"[Import] extraction error: {e}", file=sys.stderr, flush=True)
        return []


# ==================== Background Processing ====================

def _process_import_sync(import_id: str, namespace: str, file_path: str, source_type: str, source_name: str, llm_cfg: dict, user_id: str):
    """Synchronous background task to parse file, chunk, and extract knowledge."""
    conn = get_db()
    try:
        # Update status to processing
        conn.execute("UPDATE knowledge_imports SET status = 'processing' WHERE id = ?", (import_id,))
        conn.commit()

        # Parse file
        if source_type == 'pdf':
            chunks = parse_pdf(file_path)
        elif source_type == 'csv':
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                chunks = parse_csv_content(f.read())
        elif source_type == 'markdown':
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                chunks = parse_markdown(f.read())
        elif source_type in ('notion_export', 'obsidian', 'archive', 'zip'):
            detected_type, files = parse_zip(file_path)
            conn.execute("UPDATE knowledge_imports SET source_type = ? WHERE id = ?", (detected_type, import_id))
            is_obsidian = detected_type == 'obsidian'
            # Flatten all files into chunks, using folder path as context
            chunks = []
            for fname, content in files:
                if fname.lower().endswith('.csv'):
                    chunks.extend(parse_csv_content(content))
                elif fname.lower().endswith('.md'):
                    file_chunks = parse_markdown(content, obsidian_mode=is_obsidian)
                    # Extract wikilinks for context enrichment
                    if is_obsidian:
                        links = _extract_wikilinks(content)
                        if links and file_chunks:
                            file_chunks[0] = f"相关笔记: {', '.join(links[:10])}\n\n{file_chunks[0]}"
                    chunks.extend(file_chunks)
                elif fname.lower().endswith('.canvas'):
                    chunks.extend(parse_text(content))
                else:
                    chunks.extend(parse_text(content))
            source_type = detected_type
        else:
            # Default: plain text
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                chunks = parse_text(f.read())

        if not chunks:
            conn.execute(
                "UPDATE knowledge_imports SET status = 'failed', error_message = '未能从文件中提取到有效内容' WHERE id = ?",
                (import_id,)
            )
            conn.commit()
            conn.close()
            return

        # Update status to extracting
        conn.execute(
            "UPDATE knowledge_imports SET status = 'extracting', total_chunks = ? WHERE id = ?",
            (len(chunks), import_id)
        )
        conn.commit()

        total_suggestions = 0
        type_labels = {'obsidian': 'Obsidian Vault', 'notion_export': 'Notion 导出', 'markdown': 'Markdown', 'archive': '压缩包'}
        source_label = type_labels.get(source_type, source_type)
        source_context = f"导入自 {source_name}（{source_label}）" if source_name else f"导入文件（{source_label}）"

        for i, chunk in enumerate(chunks):
            try:
                items = _extract_from_chunk(chunk, source_context, llm_cfg)
                now = datetime.now().isoformat()
                for item in items:
                    sug_id = f"sug_{uuid.uuid4().hex[:12]}"
                    conn.execute("""
                        INSERT INTO knowledge_suggestions (id, namespace, user_id, session_id, summary, content_type, reason, status, created_at, import_id)
                        VALUES (?, ?, ?, '', ?, ?, ?, 'pending', ?, ?)
                    """, (sug_id, namespace, user_id, item["summary"], item.get("content_type", "事实"), item.get("reason", ""), now, import_id))
                    total_suggestions += 1

                conn.execute(
                    "UPDATE knowledge_imports SET processed_chunks = ?, total_suggestions = ? WHERE id = ?",
                    (i + 1, total_suggestions, import_id)
                )
                conn.commit()
            except Exception as e:
                print(f"[Import] chunk {i} error: {e}", file=sys.stderr, flush=True)
                # Continue processing other chunks
                conn.execute(
                    "UPDATE knowledge_imports SET processed_chunks = ? WHERE id = ?",
                    (i + 1, import_id)
                )
                conn.commit()

        # Completed
        conn.execute(
            "UPDATE knowledge_imports SET status = 'completed', completed_at = ?, total_suggestions = ? WHERE id = ?",
            (datetime.now().isoformat(), total_suggestions, import_id)
        )
        conn.commit()
        print(f"[Import] completed: {import_id}, {total_suggestions} suggestions from {len(chunks)} chunks", file=sys.stderr, flush=True)

    except Exception as e:
        print(f"[Import] fatal error: {e}", file=sys.stderr, flush=True)
        try:
            conn.execute(
                "UPDATE knowledge_imports SET status = 'failed', error_message = ? WHERE id = ?",
                (str(e)[:500], import_id)
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


async def _process_import_background(import_id: str, namespace: str, file_path: str, source_type: str, source_name: str, llm_cfg: dict, user_id: str):
    """Async wrapper that runs sync processing in executor."""
    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        await loop.run_in_executor(
            executor,
            _process_import_sync,
            import_id, namespace, file_path, source_type, source_name, llm_cfg, user_id,
        )
    finally:
        executor.shutdown(wait=False)


def _process_notion_import_sync(import_id: str, namespace: str, token: str, page_id: str, page_title: str, llm_cfg: dict, user_id: str):
    """Synchronous background task to fetch Notion page, chunk, and extract knowledge."""
    import time
    conn = get_db()
    try:
        conn.execute("UPDATE knowledge_imports SET status = 'processing' WHERE id = ?", (import_id,))
        conn.commit()

        # Fetch page content from Notion API
        content = notion_get_page_content(token, page_id)

        if not content or not content.strip():
            conn.execute(
                "UPDATE knowledge_imports SET status = 'failed', error_message = '未能从 Notion 页面提取到内容' WHERE id = ?",
                (import_id,)
            )
            conn.commit()
            conn.close()
            return

        # Parse as markdown
        chunks = parse_markdown(content)
        if not chunks:
            conn.execute(
                "UPDATE knowledge_imports SET status = 'failed', error_message = '页面内容过短，无法提取' WHERE id = ?",
                (import_id,)
            )
            conn.commit()
            conn.close()
            return

        conn.execute(
            "UPDATE knowledge_imports SET status = 'extracting', total_chunks = ? WHERE id = ?",
            (len(chunks), import_id)
        )
        conn.commit()

        total_suggestions = 0
        source_context = f"导入自 Notion 页面「{page_title}」"

        for i, chunk in enumerate(chunks):
            try:
                items = _extract_from_chunk(chunk, source_context, llm_cfg)
                now = datetime.now().isoformat()
                for item in items:
                    sug_id = f"sug_{uuid.uuid4().hex[:12]}"
                    conn.execute("""
                        INSERT INTO knowledge_suggestions (id, namespace, user_id, session_id, summary, content_type, reason, status, created_at, import_id)
                        VALUES (?, ?, ?, '', ?, ?, ?, 'pending', ?, ?)
                    """, (sug_id, namespace, user_id, item["summary"], item.get("content_type", "事实"), item.get("reason", ""), now, import_id))
                    total_suggestions += 1

                conn.execute(
                    "UPDATE knowledge_imports SET processed_chunks = ?, total_suggestions = ? WHERE id = ?",
                    (i + 1, total_suggestions, import_id)
                )
                conn.commit()
            except Exception as e:
                print(f"[Notion Import] chunk {i} error: {e}", file=sys.stderr, flush=True)
                conn.execute(
                    "UPDATE knowledge_imports SET processed_chunks = ? WHERE id = ?",
                    (i + 1, import_id)
                )
                conn.commit()

        conn.execute(
            "UPDATE knowledge_imports SET status = 'completed', completed_at = ?, total_suggestions = ? WHERE id = ?",
            (datetime.now().isoformat(), total_suggestions, import_id)
        )
        conn.commit()
        print(f"[Notion Import] completed: {import_id}, {total_suggestions} suggestions from {len(chunks)} chunks", file=sys.stderr, flush=True)

    except Exception as e:
        print(f"[Notion Import] fatal error: {e}", file=sys.stderr, flush=True)
        try:
            conn.execute(
                "UPDATE knowledge_imports SET status = 'failed', error_message = ? WHERE id = ?",
                (str(e)[:500], import_id)
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


async def _process_notion_import_background(import_id: str, namespace: str, token: str, page_id: str, page_title: str, llm_cfg: dict, user_id: str):
    """Async wrapper that runs sync Notion processing in executor."""
    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        await loop.run_in_executor(
            executor,
            _process_notion_import_sync,
            import_id, namespace, token, page_id, page_title, llm_cfg, user_id,
        )
    finally:
        executor.shutdown(wait=False)


# ==================== Endpoints ====================

ALLOWED_EXTENSIONS = {'.md', '.txt', '.pdf', '.csv', '.json'}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
MAX_ZIP_SIZE = 100 * 1024 * 1024  # 100MB


def _get_source_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return {
        '.md': 'markdown',
        '.txt': 'text',
        '.pdf': 'pdf',
        '.csv': 'csv',
        '.json': 'json',
    }.get(ext, 'text')


@router.post("/upload")
async def upload_file(
    namespace: str,
    file: UploadFile = File(...),
    user: dict = Depends(verify_namespace),
):
    """Upload a file for knowledge extraction."""
    _require_owner(user)

    # Validate extension
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}，支持: {', '.join(ALLOWED_EXTENSIONS)}")

    # Read file
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件过大，最大 {MAX_FILE_SIZE // 1024 // 1024}MB")

    # Create import record
    import_id = f"imp_{uuid.uuid4().hex[:12]}"
    source_type = _get_source_type(file.filename or 'file.txt')
    user_id = user.get("user_id", "")

    # Save file
    upload_dir = DATA_DIR / import_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / (file.filename or 'upload.txt')
    with open(file_path, 'wb') as f:
        f.write(content)

    # Create DB record
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO knowledge_imports (id, namespace, user_id, source_type, source_name, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (import_id, namespace, user_id, source_type, file.filename, now))
    conn.commit()
    conn.close()

    # Get LLM config
    llm_cfg = _get_agent_llm_config(namespace)
    if not llm_cfg.get("api_key"):
        # Update status to failed
        conn = get_db()
        conn.execute("UPDATE knowledge_imports SET status = 'failed', error_message = '请先在 Config 中配置 LLM API Key' WHERE id = ?", (import_id,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="请先在 Config 中配置 LLM API Key")

    # Start background processing
    asyncio.create_task(_process_import_background(
        import_id, namespace, str(file_path), source_type, file.filename, llm_cfg, user_id
    ))

    return {"import_id": import_id, "status": "pending", "source_type": source_type, "source_name": file.filename}


@router.post("/upload-zip")
async def upload_zip(
    namespace: str,
    file: UploadFile = File(...),
    user: dict = Depends(verify_namespace),
):
    """Upload a zip file (Notion export / Obsidian vault)."""
    _require_owner(user)

    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext != '.zip':
        raise HTTPException(status_code=400, detail="仅支持 .zip 文件")

    content = await file.read()
    if len(content) > MAX_ZIP_SIZE:
        raise HTTPException(status_code=400, detail=f"文件过大，最大 {MAX_ZIP_SIZE // 1024 // 1024}MB")

    import_id = f"imp_{uuid.uuid4().hex[:12]}"
    user_id = user.get("user_id", "")

    upload_dir = DATA_DIR / import_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / (file.filename or 'upload.zip')
    with open(file_path, 'wb') as f:
        f.write(content)

    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO knowledge_imports (id, namespace, user_id, source_type, source_name, status, created_at)
        VALUES (?, ?, ?, 'zip', ?, 'pending', ?)
    """, (import_id, namespace, user_id, file.filename, now))
    conn.commit()
    conn.close()

    llm_cfg = _get_agent_llm_config(namespace)
    if not llm_cfg.get("api_key"):
        conn = get_db()
        conn.execute("UPDATE knowledge_imports SET status = 'failed', error_message = '请先配置 LLM API Key' WHERE id = ?", (import_id,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="请先在 Config 中配置 LLM API Key")

    asyncio.create_task(_process_import_background(
        import_id, namespace, str(file_path), 'zip', file.filename, llm_cfg, user_id
    ))

    return {"import_id": import_id, "status": "pending", "source_type": "zip", "source_name": file.filename}


@router.get("")
async def list_imports(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """List all imports for this namespace."""
    _require_owner(user)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, source_type, source_name, status, total_chunks, processed_chunks,
               total_suggestions, error_message, created_at, completed_at
        FROM knowledge_imports
        WHERE namespace = ?
        ORDER BY created_at DESC
        LIMIT 50
    """, (namespace,))
    rows = cursor.fetchall()
    conn.close()

    return {
        "imports": [
            {
                "id": r["id"],
                "source_type": r["source_type"],
                "source_name": r["source_name"],
                "status": r["status"],
                "total_chunks": r["total_chunks"],
                "processed_chunks": r["processed_chunks"],
                "total_suggestions": r["total_suggestions"],
                "error_message": r["error_message"],
                "created_at": r["created_at"],
                "completed_at": r["completed_at"],
            }
            for r in rows
        ]
    }


@router.get("/{import_id}")
async def get_import(
    namespace: str,
    import_id: str,
    user: dict = Depends(verify_namespace),
):
    """Get import detail with progress."""
    _require_owner(user)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, source_type, source_name, status, total_chunks, processed_chunks,
               total_suggestions, error_message, created_at, completed_at
        FROM knowledge_imports
        WHERE id = ? AND namespace = ?
    """, (import_id, namespace))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="导入记录不存在")

    return {
        "id": row["id"],
        "source_type": row["source_type"],
        "source_name": row["source_name"],
        "status": row["status"],
        "total_chunks": row["total_chunks"],
        "processed_chunks": row["processed_chunks"],
        "total_suggestions": row["total_suggestions"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


@router.delete("/{import_id}")
async def delete_import(
    namespace: str,
    import_id: str,
    user: dict = Depends(verify_namespace),
):
    """Delete import record and associated pending suggestions."""
    _require_owner(user)
    conn = get_db()
    cursor = conn.cursor()

    # Verify it exists
    cursor.execute("SELECT id FROM knowledge_imports WHERE id = ? AND namespace = ?", (import_id, namespace))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="导入记录不存在")

    # Delete pending suggestions
    cursor.execute(
        "DELETE FROM knowledge_suggestions WHERE import_id = ? AND status = 'pending'",
        (import_id,)
    )
    deleted_suggestions = cursor.rowcount

    # Delete import record
    cursor.execute("DELETE FROM knowledge_imports WHERE id = ?", (import_id,))
    conn.commit()
    conn.close()

    # Clean up files
    import shutil
    upload_dir = DATA_DIR / import_id
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)

    return {"success": True, "deleted_suggestions": deleted_suggestions}


# ==================== Notion Integration Endpoints ====================


@router.post("/notion/connect")
async def notion_connect(
    namespace: str,
    user: dict = Depends(verify_namespace),
    body: dict = Body(...),
):
    """Connect a Notion integration token."""
    _require_owner(user)
    import httpx

    token = body.get("token", "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="请提供 Notion Integration Token")

    # Validate token by calling Notion /users/me
    try:
        resp = httpx.get(
            f"{NOTION_API_BASE}/users/me",
            headers=_notion_headers(token),
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Token 无效，请检查是否正确复制了 Internal Integration Token")
        user_data = resp.json()
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="无法连接 Notion API，请稍后重试")

    # Extract workspace name from bot info
    workspace_name = ""
    if user_data.get("type") == "bot":
        workspace_name = user_data.get("bot", {}).get("workspace_name", "")
    if not workspace_name:
        workspace_name = user_data.get("name", "Notion Workspace")

    user_id = user.get("user_id", "")
    conn = get_db()

    # Upsert: delete old connection for this namespace, insert new
    conn.execute("DELETE FROM notion_connections WHERE namespace = ?", (namespace,))
    conn_id = f"nc_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO notion_connections (id, namespace, user_id, notion_token, workspace_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (conn_id, namespace, user_id, token, workspace_name, now))
    conn.commit()
    conn.close()

    return {"success": True, "workspace_name": workspace_name}


@router.get("/notion/pages")
async def notion_pages(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """List pages accessible to the connected Notion integration."""
    _require_owner(user)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT notion_token, workspace_name FROM notion_connections WHERE namespace = ?", (namespace,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="未连接 Notion，请先连接")

    token = row["notion_token"]
    pages = notion_search_pages(token)

    return {"pages": pages, "workspace_name": row["workspace_name"]}


@router.post("/notion/import")
async def notion_import(
    namespace: str,
    user: dict = Depends(verify_namespace),
    body: dict = Body(...),
):
    """Import selected Notion pages."""
    _require_owner(user)

    page_ids = body.get("page_ids", [])
    if not page_ids:
        raise HTTPException(status_code=400, detail="请选择要导入的页面")

    # Get saved token
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT notion_token FROM notion_connections WHERE namespace = ?", (namespace,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="未连接 Notion，请先连接")
    token = row["notion_token"]

    # Get LLM config
    llm_cfg = _get_agent_llm_config(namespace)
    if not llm_cfg.get("api_key"):
        conn.close()
        raise HTTPException(status_code=400, detail="请先在 Config 中配置 LLM API Key")

    user_id = user.get("user_id", "")

    # Get page titles for each page_id
    all_pages = notion_search_pages(token)
    page_map = {p["id"]: p for p in all_pages}

    imports = []
    now = datetime.now().isoformat()

    for pid in page_ids:
        page_info = page_map.get(pid, {})
        page_title = page_info.get("title", "Untitled")

        import_id = f"imp_{uuid.uuid4().hex[:12]}"
        conn.execute("""
            INSERT INTO knowledge_imports (id, namespace, user_id, source_type, source_name, status, created_at)
            VALUES (?, ?, ?, 'notion_api', ?, 'pending', ?)
        """, (import_id, namespace, user_id, page_title, now))
        imports.append({"import_id": import_id, "page_title": page_title})

    conn.commit()
    conn.close()

    # Start background processing for each page
    for i, imp in enumerate(imports):
        pid = page_ids[i]
        asyncio.create_task(_process_notion_import_background(
            imp["import_id"], namespace, token, pid, imp["page_title"], llm_cfg, user_id
        ))

    return {"imports": imports}


@router.delete("/notion/disconnect")
async def notion_disconnect(
    namespace: str,
    user: dict = Depends(verify_namespace),
):
    """Disconnect Notion integration."""
    _require_owner(user)
    conn = get_db()
    conn.execute("DELETE FROM notion_connections WHERE namespace = ?", (namespace,))
    conn.commit()
    conn.close()
    return {"success": True}
