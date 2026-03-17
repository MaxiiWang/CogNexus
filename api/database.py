"""
Hub Database Module
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "hub.db"


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            tx_type TEXT CHECK (tx_type IN ('purchase', 'reward', 'topup', 'register')),
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    
    # Simulation 相关表 (独立迁移)
    from migrate_simulation import migrate as migrate_sim
    migrate_sim()
    
    print("✅ 数据库初始化完成")


if __name__ == "__main__":
    init_db()
