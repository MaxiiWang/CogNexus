"""
Telegram Webhook — receive messages, route to chat, reply back
"""
import json
import uuid
import httpx
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, HTTPException

from database import get_db

router = APIRouter(tags=["telegram"])

# Telegram message limit
TG_MAX_LEN = 4000  # leave room for formatting


# ==================== Webhook Endpoint ====================

@router.post("/api/telegram/webhook/{agent_id}")
async def telegram_webhook(agent_id: str, request: Request):
    """Receive Telegram updates via webhook"""
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    msg = update.get("message", {})
    text = msg.get("text", "").strip()
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    from_user = msg.get("from", {})

    if not text or not chat_id:
        return {"ok": True}

    # Handle Telegram /start command (welcome message, not a knowledge query)
    if text == "/start":
        conn = get_db()
        agent = conn.execute("SELECT name, description, im_config FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        conn.close()
        if agent:
            im_config = json.loads(agent["im_config"] or "{}")
            bot_token = im_config.get("telegram", {}).get("bot_token", "")
            if bot_token:
                name = agent["name"] or "Agent"
                desc = agent["description"] or ""
                welcome = f"👋 你好！我是 {name}。\n\n{desc}\n\n直接发消息即可开始对话。"
                asyncio.create_task(_send_message(bot_token, chat_id, welcome))
        return {"ok": True}

    # Find agent + verify chat_id matches im_config
    conn = get_db()
    agent = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    conn.close()

    if not agent:
        return {"ok": True}

    im_config = json.loads(agent["im_config"] or "{}")
    tg = im_config.get("telegram", {})
    bot_token = tg.get("bot_token", "")
    expected_chat_id = tg.get("chat_id", "")

    if not bot_token or chat_id != expected_chat_id:
        return {"ok": True}  # Ignore messages from unknown users

    # Process in background to return 200 quickly
    asyncio.create_task(_handle_message(
        agent_id=agent_id,
        agent=dict(agent),
        bot_token=bot_token,
        chat_id=chat_id,
        text=text,
        from_user=from_user,
    ))

    return {"ok": True}


async def _handle_message(agent_id: str, agent: dict, bot_token: str, chat_id: str, text: str, from_user: dict):
    """Process incoming message: get/create session, call chat, reply"""
    try:
        namespace = agent.get("namespace", "default")
        owner_id = agent["owner_id"]

        # 1. Get or create Telegram session
        session_id = _get_or_create_session(agent_id, owner_id, channel="telegram")

        # 2. Send "typing" indicator
        await _send_typing(bot_token, chat_id)

        # 3. Call chat endpoint internally (collect full response)
        full_response = await _call_chat(namespace, text, session_id, owner_id)

        if not full_response:
            full_response = "抱歉，处理消息时出错了。"

        # 4. Check for pending knowledge suggestions
        suggestion_hint = _check_suggestions(namespace, owner_id)
        if suggestion_hint:
            full_response += f"\n\n{suggestion_hint}"

        # 5. Send reply (split if needed)
        await _send_reply(bot_token, chat_id, full_response)

    except Exception as e:
        print(f"[TG Webhook] Error handling message: {e}")
        import traceback
        traceback.print_exc()
        try:
            await _send_message(bot_token, chat_id, f"⚠️ 处理消息时出错: {str(e)[:200]}")
        except Exception:
            pass


def _get_or_create_session(agent_id: str, user_id: str, channel: str = "telegram") -> str:
    """Get existing Telegram session or create one"""
    conn = get_db()

    # Look for existing session with channel marker in title
    session = conn.execute("""
        SELECT session_id FROM chat_sessions
        WHERE agent_id = ? AND user_id = ? AND title = ?
        ORDER BY updated_at DESC LIMIT 1
    """, (agent_id, user_id, f"__im_{channel}__")).fetchone()

    if session:
        conn.close()
        return session["session_id"]

    # Create new session
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO chat_sessions (session_id, agent_id, user_id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (session_id, agent_id, user_id, f"__im_{channel}__", now, now))
    conn.commit()
    conn.close()

    print(f"[TG Webhook] Created session {session_id} for agent {agent_id}")
    return session_id


async def _call_chat(namespace: str, question: str, session_id: str, user_id: str) -> str:
    """Call chat logic — mirrors web chat/stream pipeline exactly"""
    try:
        from cogmate_core import CogmateAgent
        from routes.knowledge import (
            _build_dynamic_system_prompt, _graph_reasoning,
            _web_search_fallback, _get_agent_llm_config, _get_chat_config, _get_agent_info
        )

        # Slash commands
        if question.startswith('/'):
            try:
                from cogmate_core.intent_handler import IntentHandler
                handler = IntentHandler(namespace=namespace)
                return handler._handle_slash_command(question)
            except Exception as e:
                return f"命令执行失败: {e}"

        chat_config = _get_chat_config(namespace)
        llm_cfg = _get_agent_llm_config(namespace)
        agent_info = _get_agent_info(namespace)
        is_character = agent_info.get("agent_type") == "character"

        if not llm_cfg.get("api_key"):
            return "⚠️ 请先在网页端 Config 中配置 LLM API Key"

        # Load session context
        context_messages = []
        try:
            conn = get_db()
            rows = conn.execute("""
                SELECT role, content FROM chat_messages
                WHERE session_id = ? ORDER BY created_at DESC LIMIT ?
            """, (session_id, chat_config.get("context_rounds", 10))).fetchall()
            context_messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
            conn.close()
        except Exception:
            pass

        # Vector search
        top_k = chat_config.get("retrieval_top_k", 5)
        min_score = chat_config.get("retrieval_min_score", 0.5)
        try:
            cogmate = CogmateAgent(namespace=namespace)
            results = cogmate.query(query_text=question, top_k=top_k, min_score=min_score)
            vector_results = results.get("vector_results", [])
        except Exception:
            vector_results = []

        # Graph reasoning expansion (respects config switch)
        if chat_config.get("enable_graph_reasoning") and vector_results:
            try:
                fact_ids = [f.get("fact_id", "") for f in vector_results if f.get("fact_id")]
                graph_extra = _graph_reasoning(namespace, fact_ids)
                if graph_extra:
                    vector_results = vector_results + graph_extra
            except Exception:
                pass

        # Web search enhancement
        web_suggestions = []
        if chat_config.get("enable_web_search"):
            try:
                web_results = _web_search_fallback(question)
                if web_results:
                    web_suggestions = [{"summary": w["summary"], "content_type": w["content_type"]} for w in web_results]
                    vector_results = vector_results + web_results
            except Exception:
                pass

        # Human Agent strict mode: no knowledge = no answer
        if not vector_results and not is_character:
            answer = "📭 知识库中暂无相关内容。"
            if chat_config.get("enable_web_search"):
                answer += "\n（已开启网络搜索但未找到相关结果）"
            _save_messages(session_id, question, answer, 0)
            return answer

        # Build system prompt
        system_prompt = _build_dynamic_system_prompt(namespace, vector_results, chat_config, context_messages)

        # Build LLM request
        provider = llm_cfg.get("provider", "")
        base_url = llm_cfg.get("base_url") or llm_cfg.get("endpoint", "")
        if not base_url:
            provider_urls = {
                "openai": "https://api.openai.com/v1",
                "doubao": "https://ark.cn-beijing.volces.com/api/v3",
                "deepseek": "https://api.deepseek.com/v1",
                "moonshot": "https://api.moonshot.cn/v1",
            }
            base_url = provider_urls.get(provider, "https://api.openai.com/v1")

        messages = [
            {"role": "system", "content": system_prompt},
            *context_messages,
            {"role": "user", "content": question},
        ]

        # Call LLM (non-streaming)
        answer = ""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {llm_cfg['api_key']}", "Content-Type": "application/json"},
                json={
                    "model": llm_cfg.get("model", "gpt-4o-mini"),
                    "messages": messages,
                    "max_tokens": chat_config.get("max_tokens", 2000),
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    answer = choices[0].get("message", {}).get("content", "")
                    rc = choices[0].get("message", {}).get("reasoning_content")
                    if rc and not answer:
                        answer = rc
            else:
                print(f"[TG Chat] LLM {resp.status_code}: {resp.text[:200]}")
                answer = f"⚠️ LLM 返回错误 ({resp.status_code})"

        if not answer:
            if vector_results:
                from cogmate_core.llm_answer import _structured_answer
                answer = _structured_answer(question, vector_results,
                    "\n".join([f.get("summary", "") for f in vector_results]), namespace)
            else:
                answer = "知识库中没有找到直接相关的信息。"

        # Save messages
        _save_messages(session_id, question, answer, len(vector_results))

        # Background knowledge extraction (full params, matching web chat)
        if llm_cfg.get("api_key") and len(question) + len(answer) > 50:
            asyncio.create_task(_extract_knowledge_bg(
                namespace, user_id, session_id, question, answer,
                llm_cfg, web_suggestions, context_messages
            ))

        return answer

    except Exception as e:
        print(f"[TG Chat] Error: {e}")
        import traceback
        traceback.print_exc()
        return ""


def _save_messages(session_id: str, user_msg: str, assistant_msg: str, sources_count: int = 0):
    """Save user + assistant messages to session"""
    try:
        conn = get_db()
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO chat_messages (message_id, session_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
            (f"msg_{uuid.uuid4().hex[:12]}", session_id, user_msg, now)
        )
        if assistant_msg:
            conn.execute(
                "INSERT INTO chat_messages (message_id, session_id, role, content, sources_count, created_at) VALUES (?, ?, 'assistant', ?, ?, ?)",
                (f"msg_{uuid.uuid4().hex[:12]}", session_id, assistant_msg, sources_count, now)
            )
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ?, message_count = message_count + 2 WHERE session_id = ?",
            (now, session_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TG Webhook] Save messages error: {e}")


async def _extract_knowledge_bg(namespace, user_id, session_id, question, answer, llm_cfg, web_suggestions=None, context_messages=None):
    """Background knowledge extraction (same as web chat)"""
    try:
        from routes.knowledge import _extract_knowledge_suggestions_sync
        import asyncio

        suggestions = await asyncio.get_event_loop().run_in_executor(
            None,
            _extract_knowledge_suggestions_sync,
            question, answer, web_suggestions or [], namespace, llm_cfg, context_messages or [], ""
        )

        if suggestions:
            conn = get_db()
            now = datetime.now().isoformat()
            for item in suggestions:
                sug_id = f"sug_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT INTO knowledge_suggestions (id, namespace, user_id, session_id, summary, content_type, reason, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """, (sug_id, namespace, user_id, session_id,
                      item["summary"], item.get("content_type", "事实"), item.get("reason", ""), now))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[TG Webhook] Knowledge extraction error: {e}")


def _check_suggestions(namespace: str, user_id: str) -> str:
    """Check pending suggestion count"""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM knowledge_suggestions WHERE namespace = ? AND user_id = ? AND status = 'pending'",
            (namespace, user_id)
        ).fetchone()
        conn.close()
        count = row["c"] if row else 0
        if count > 0:
            return f"💡 有 {count} 条知识建议待处理，请前往网页端查看"
        return ""
    except Exception:
        return ""


# ==================== Telegram API Helpers ====================

async def _send_typing(bot_token: str, chat_id: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"}
            )
    except Exception:
        pass


async def _send_message(bot_token: str, chat_id: str, text: str, use_format: bool = True):
    """Send a single message. Falls back to plain text if formatting fails."""
    from im_formatter import format_for_telegram

    if use_format:
        formatted = format_for_telegram(text)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": formatted, "parse_mode": "MarkdownV2"}
            )
            if resp.status_code == 200:
                return
            # MarkdownV2 failed, fallback to plain
            print(f"[TG] MarkdownV2 failed ({resp.status_code}), falling back to plain")

    # Plain text fallback
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text}
        )


async def _send_reply(bot_token: str, chat_id: str, text: str):
    """Send reply, splitting into multiple messages if needed"""
    if len(text) <= TG_MAX_LEN:
        await _send_message(bot_token, chat_id, text)
        return

    # Split by paragraphs, then by length
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TG_MAX_LEN:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(0.5)
        try:
            await _send_message(bot_token, chat_id, chunk)
        except Exception as e:
            print(f"[TG] Failed to send chunk {i}: {e}")
            try:
                await _send_message(bot_token, chat_id, chunk, use_format=False)
            except Exception:
                pass


# ==================== Webhook Registration ====================

async def register_webhook(bot_token: str, agent_id: str, base_url: str) -> dict:
    """Register Telegram webhook for an agent"""
    webhook_url = f"{base_url}/api/telegram/webhook/{agent_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Delete existing webhook first
            await client.post(f"https://api.telegram.org/bot{bot_token}/deleteWebhook")
            # Set new webhook
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/setWebhook",
                json={"url": webhook_url, "allowed_updates": ["message"]}
            )
            resp.raise_for_status()
            result = resp.json()
            print(f"[TG] Webhook registered: {webhook_url} -> {result}")
            return result
    except Exception as e:
        print(f"[TG] Webhook registration failed: {e}")
        return {"ok": False, "error": str(e)}


async def unregister_webhook(bot_token: str) -> dict:
    """Remove Telegram webhook"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"https://api.telegram.org/bot{bot_token}/deleteWebhook")
            return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}
