"""
Hub Database Module
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "hub.db"


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 用户表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            atp_balance INTEGER DEFAULT 100,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Agent 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            agent_type TEXT CHECK (agent_type IN ('human', 'character')) DEFAULT 'human',
            endpoint_url TEXT NOT NULL,
            avatar_url TEXT,
            tags TEXT,
            status TEXT DEFAULT 'active',
            last_health_check TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(user_id)
        )
    """)
    
    # Agent Token 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_tokens (
            token_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            token_value TEXT NOT NULL,
            permissions TEXT NOT NULL,
            scope TEXT DEFAULT 'unknown',
            scope_label TEXT,
            qa_limit INTEGER DEFAULT 0,
            qa_used INTEGER DEFAULT 0,
            expires_at TEXT,
            unit_price REAL DEFAULT 0,
            is_sold INTEGER DEFAULT 0,
            sold_to_user_id TEXT,
            sold_at TEXT,
            validated INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )
    """)
    
    # 已购买 Token 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchased_tokens (
            purchase_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            token_value TEXT NOT NULL,
            permissions TEXT NOT NULL,
            atp_spent INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )
    """)
    
    # 交易记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            tx_id TEXT PRIMARY KEY,
            from_user_id TEXT,
            to_user_id TEXT,
            agent_id TEXT,
            atp_amount INTEGER NOT NULL,
            tx_type TEXT CHECK (tx_type IN ('purchase', 'reward', 'topup', 'register', 'chat_fee')),
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

    # Knowledge / Settings 相关迁移
    migrate_knowledge_schema()

    # Simulation 相关表 (独立迁移)
    from migrate_simulation import migrate as migrate_sim
    migrate_sim()

    print("✅ 数据库初始化完成")


def migrate_knowledge_schema():
    """知识管理 & Settings 相关的 Schema 迁移"""
    conn = get_db()
    cursor = conn.cursor()

    # agents 表扩展：im_config, llm_config
    cursor.execute("PRAGMA table_info(agents)")
    columns = {row[1] for row in cursor.fetchall()}

    if "im_config" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN im_config TEXT DEFAULT '{}'")
    if "llm_config" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN llm_config TEXT DEFAULT '{}'")
    if "is_public" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN is_public INTEGER DEFAULT 0")
    if "avatar_model_url" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN avatar_model_url TEXT")
    if "price_per_chat" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN price_per_chat INTEGER DEFAULT 0")
    if "chat_config" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN chat_config TEXT DEFAULT '{}'")

    # simulations 表扩展：is_public
    cursor.execute("PRAGMA table_info(simulations)")
    sim_columns = {row[1] for row in cursor.fetchall()}
    if "is_public" not in sim_columns:
        cursor.execute("ALTER TABLE simulations ADD COLUMN is_public INTEGER DEFAULT 1")

    # 用户全局配置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT PRIMARY KEY,
            default_llm_provider TEXT,
            default_llm_key_encrypted TEXT,
            default_model TEXT,
            ui_language TEXT DEFAULT 'en',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 对话会话表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            title TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 对话消息表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
        )
    """)

    # Knowledge suggestions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_suggestions (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            user_id TEXT NOT NULL,
            session_id TEXT,
            summary TEXT NOT NULL,
            content_type TEXT DEFAULT '事实',
            reason TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'dismissed')),
            created_at TEXT NOT NULL,
            processed_at TEXT
        )
    """)

    # Migration: add import_id column to knowledge_suggestions
    try:
        cursor.execute("ALTER TABLE knowledge_suggestions ADD COLUMN import_id TEXT")
    except Exception:
        pass  # Column already exists

    # Knowledge imports table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_imports (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            user_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_name TEXT,
            source_meta TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'extracting', 'completed', 'failed')),
            total_chunks INTEGER DEFAULT 0,
            processed_chunks INTEGER DEFAULT 0,
            total_suggestions INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_imports_ns ON knowledge_imports(namespace, created_at DESC)")

    # 索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_agent_user ON chat_sessions(agent_id, user_id, updated_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_suggestions_ns_status ON knowledge_suggestions(namespace, status)")

    # Notion connections table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notion_connections (
            id TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            user_id TEXT NOT NULL,
            notion_token TEXT NOT NULL,
            workspace_name TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Agent scheduled tasks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_tasks (
            task_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            task_type TEXT NOT NULL CHECK(task_type IN (
                'briefing', 'knowledge_digest', 'stress_test',
                'expiry_scan', 'graph_health', 'cleanup_scan', 'custom'
            )),
            enabled INTEGER DEFAULT 1,
            schedule TEXT NOT NULL,
            config TEXT DEFAULT '{}',
            last_run_at TEXT,
            last_status TEXT CHECK(last_status IN ('success', 'failed', 'running')),
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_agent ON agent_tasks(agent_id)")

    # Agent insights (task outputs)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_insights (
            insight_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            task_id TEXT,
            task_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            metadata TEXT DEFAULT '{}',
            status TEXT DEFAULT 'unread' CHECK(status IN ('unread', 'read', 'archived')),
            push_status TEXT DEFAULT 'pending' CHECK(push_status IN ('pending', 'pushed', 'no_im', 'push_failed')),
            push_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
            FOREIGN KEY (task_id) REFERENCES agent_tasks(task_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_insights_agent ON agent_insights(agent_id, created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_insights_status ON agent_insights(agent_id, status)")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
