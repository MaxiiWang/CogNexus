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

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from database import get_db
from routes.knowledge import verify_namespace, _require_owner, _get_agent_llm_config

router = APIRouter(prefix="/api/knowledge/{namespace}/imports", tags=["knowledge-imports"])

# Upload storage directory
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "imports"

# ==================== Parsers ====================

def parse_markdown(content: str) -> list:
    """Split markdown by ## headings into chunks."""
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
        elif len(s) > 2000:
            # Split long sections at sentence boundaries
            sentences = re.split(r'(?<=[。！？.!?\n])\s*', s)
            buf = ''
            for sent in sentences:
                if len(buf) + len(sent) > 500 and buf:
                    chunks.append(buf.strip())
                    buf = sent
                else:
                    buf += sent
            if buf.strip():
                chunks.append(buf.strip())
        else:
            chunks.append(s)
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
        if len(buf) + len(p) < 500:
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


def parse_zip(file_path: str) -> tuple:
    """Parse zip file, detect type (Notion/Obsidian/archive), extract text files."""
    detected_type = 'archive'
    files = []

    with zipfile.ZipFile(file_path, 'r') as zf:
        names = zf.namelist()

        # Detect type
        has_obsidian = any('.obsidian/' in n for n in names)
        has_canvas = any(n.endswith('.canvas') for n in names)
        has_notion_uuids = any(re.search(r'[a-f0-9]{32}', n) for n in names if n.endswith('.md'))

        if has_obsidian or has_canvas:
            detected_type = 'obsidian'
        elif has_notion_uuids:
            detected_type = 'notion_export'

        for name in names:
            lower = name.lower()
            if lower.endswith(('.md', '.txt', '.csv')):
                try:
                    content = zf.read(name).decode('utf-8', errors='replace')
                    if content.strip():
                        files.append((name, content))
                except Exception:
                    pass
            elif lower.endswith('.canvas'):
                try:
                    raw = zf.read(name).decode('utf-8', errors='replace')
                    canvas = json.loads(raw)
                    texts = [n.get('text', '') for n in canvas.get('nodes', []) if n.get('text')]
                    if texts:
                        files.append((name, '\n\n'.join(texts)))
                except Exception:
                    pass

    return detected_type, files


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

    extraction_prompt = f"""提取知识条目。每条应独立、自包含。content_type: 事实/观点/决策/资讯/洞察

来源: {source_context}
内容: {chunk_text[:1500]}

输出JSON数组: [{{"summary":"..","content_type":"..","reason":".."}}]
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
                    {"role": "system", "content": "你是知识提取引擎。只输出JSON数组。无内容则输出[]。不要输出任何其他文字。"},
                    {"role": "user", "content": extraction_prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1000,
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
        return valid[:5]

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
            # Flatten all files into chunks
            chunks = []
            for fname, content in files:
                if fname.lower().endswith('.csv'):
                    chunks.extend(parse_csv_content(content))
                elif fname.lower().endswith('.md'):
                    chunks.extend(parse_markdown(content))
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
        source_context = f"导入自 {source_name}" if source_name else f"导入文件({source_type})"

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
