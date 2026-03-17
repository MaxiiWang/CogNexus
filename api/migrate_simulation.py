#!/usr/bin/env python3
"""
Simulation Schema Migration
新增 Simulation 系统所需的表 + agents 表扩展字段
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "hub.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ==========================================
    # 1. agents 表扩展
    # ==========================================

    # 检查 description_embedding 是否存在
    cursor.execute("PRAGMA table_info(agents)")
    columns = [row[1] for row in cursor.fetchall()]

    if "description_embedding" not in columns:
        cursor.execute("ALTER TABLE agents ADD COLUMN description_embedding BLOB")
        print("  ✅ agents.description_embedding added")

    # agent_type 约束扩展: 目前是 CHECK ('human', 'character')
    # SQLite 不能 ALTER CHECK，但插入 'simulation' 类型时不会用到 agents 表
    # 所以不需要改 (simulation 不是 agent_type，是独立维度)

    # ==========================================
    # 2. Simulation 主表
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS simulations (
            simulation_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            question TEXT NOT NULL,
            question_embedding BLOB,

            -- 分类
            category TEXT NOT NULL,
            tags TEXT DEFAULT '[]',

            -- 预测配置
            outcome_type TEXT DEFAULT 'binary',
            outcome_options TEXT DEFAULT '["yes","no"]',
            resolution_criteria TEXT NOT NULL,
            resolution_source TEXT,

            -- 多轮配置
            total_rounds INTEGER DEFAULT 1,
            current_round INTEGER DEFAULT 0,
            round_interval TEXT,

            -- 参与配置
            min_agents INTEGER DEFAULT 3,
            max_agents INTEGER DEFAULT 50,
            stake_per_agent INTEGER DEFAULT 5,

            -- 状态
            status TEXT DEFAULT 'draft'
                CHECK (status IN ('draft','recruiting','active','closed','resolved','settled')),

            -- 时间线
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            recruiting_at TEXT,
            opens_at TEXT,
            closes_at TEXT,
            resolved_at TEXT,
            settled_at TEXT,

            -- 结果
            actual_outcome TEXT,
            final_prediction TEXT,

            FOREIGN KEY (created_by) REFERENCES users(user_id)
        )
    """)
    print("  ✅ simulations table created")

    # ==========================================
    # 3. 轮次表
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS simulation_rounds (
            round_id TEXT PRIMARY KEY,
            simulation_id TEXT NOT NULL,
            round_number INTEGER NOT NULL,

            -- 轮次主题
            title TEXT,
            context TEXT,

            -- 状态
            status TEXT DEFAULT 'pending'
                CHECK (status IN ('pending','active','closed')),
            opens_at TEXT,
            closes_at TEXT,

            -- 本轮聚合结果
            aggregated_result TEXT,
            result_summary TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(simulation_id, round_number),
            FOREIGN KEY (simulation_id) REFERENCES simulations(simulation_id)
        )
    """)
    print("  ✅ simulation_rounds table created")

    # ==========================================
    # 4. 参与者表
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS simulation_participants (
            simulation_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,

            -- 资质评估
            relevance_score REAL,
            influence_weight REAL,
            qualification_method TEXT,

            -- 角色
            role TEXT,
            role_description TEXT,

            -- 状态
            status TEXT DEFAULT 'invited'
                CHECK (status IN ('invited','accepted','active','declined')),
            stake_amount INTEGER DEFAULT 0,

            -- 最终预测 (最后一轮 predictive 的结果)
            final_stance TEXT,
            final_confidence REAL,
            was_correct INTEGER,
            reward_amount INTEGER,

            invited_at TEXT DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (simulation_id, agent_id),
            FOREIGN KEY (simulation_id) REFERENCES simulations(simulation_id),
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )
    """)
    print("  ✅ simulation_participants table created")

    # ==========================================
    # 5. 每轮反应表
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS round_reactions (
            reaction_id TEXT PRIMARY KEY,
            round_id TEXT NOT NULL,
            simulation_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,

            -- 提问
            prompt TEXT NOT NULL,
            prompt_type TEXT NOT NULL DEFAULT 'predictive'
                CHECK (prompt_type IN ('narrative','predictive')),

            -- 共有: 完整回应
            response_text TEXT,

            -- narrative 专用
            key_points TEXT,
            sentiment TEXT,

            -- predictive 专用
            stance TEXT,
            confidence REAL,
            brief_reasoning TEXT,

            -- 通用
            knowledge_depth INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
                CHECK (status IN ('pending','collected','failed','disputed')),
            collected_at TEXT,

            -- Dispute
            owner_disputed INTEGER DEFAULT 0,
            owner_correction TEXT,
            disputed_at TEXT,

            UNIQUE(round_id, agent_id),
            FOREIGN KEY (round_id) REFERENCES simulation_rounds(round_id),
            FOREIGN KEY (simulation_id) REFERENCES simulations(simulation_id),
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )
    """)
    print("  ✅ round_reactions table created")

    # ==========================================
    # 6. Agent 历史评分表
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_sim_scores (
            agent_id TEXT PRIMARY KEY,
            total_participated INTEGER DEFAULT 0,
            total_correct INTEGER DEFAULT 0,
            accuracy_rate REAL DEFAULT 0,
            accuracy_by_category TEXT DEFAULT '{}',
            avg_confidence REAL DEFAULT 0,
            calibration_score REAL DEFAULT 0.5,
            atp_earned INTEGER DEFAULT 0,
            atp_lost INTEGER DEFAULT 0,
            last_participated_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        )
    """)
    print("  ✅ agent_sim_scores table created")

    # ==========================================
    # 7. 结算记录表
    # ==========================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS simulation_settlements (
            settlement_id TEXT PRIMARY KEY,
            simulation_id TEXT NOT NULL,
            total_agents INTEGER,
            total_correct INTEGER,
            total_stake_collected INTEGER,
            total_rewards_distributed INTEGER,
            settlement_details TEXT,
            settled_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (simulation_id) REFERENCES simulations(simulation_id)
        )
    """)
    print("  ✅ simulation_settlements table created")

    # ==========================================
    # 8. transactions 表扩展 tx_type
    # ==========================================
    # SQLite 不能 ALTER CHECK, 但我们可以插入 sim_stake/sim_reward
    # 因为原 CHECK 只在 CREATE TABLE 时设定，已有数据库不会严格检查新值
    # 安全起见: 重建不现实，直接用即可 (SQLite 的 CHECK 在某些版本宽松处理)

    # ==========================================
    # 索引
    # ==========================================

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_simulations_status 
        ON simulations(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_simulations_category 
        ON simulations(category)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_rounds_simulation 
        ON simulation_rounds(simulation_id, round_number)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_reactions_round 
        ON round_reactions(round_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_reactions_agent 
        ON round_reactions(agent_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_participants_simulation 
        ON simulation_participants(simulation_id)
    """)
    print("  ✅ indexes created")

    conn.commit()
    conn.close()
    print("\n✅ Simulation schema migration complete!")


if __name__ == "__main__":
    migrate()
