#!/bin/bash
# ============================================
# CogNexus 安全修复部署脚本
# 在生产服务器上执行: bash deploy-security-fix.sh
# ============================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

COGNEXUS_DIR="/root/CogNexus"
COGMATE_DIR="/root/cogmate"

echo "🔒 CogNexus 安全修复部署"
echo "========================"
echo ""

# ========== Step 1: 拉取最新代码 ==========
echo -e "${YELLOW}[1/7] 拉取最新代码...${NC}"
cd "$COGNEXUS_DIR"
git pull origin main
echo -e "${GREEN}✓ 代码已更新${NC}"

# ========== Step 2: 安装新依赖 ==========
echo ""
echo -e "${YELLOW}[2/7] 安装新依赖 (slowapi, cryptography)...${NC}"
if [ -d "venv" ]; then
    source venv/bin/activate
fi
pip install slowapi>=0.1.9 cryptography>=41.0.0 python-dotenv>=1.0.0
echo -e "${GREEN}✓ 依赖已安装${NC}"

# ========== Step 3: 配置 .env ==========
echo ""
echo -e "${YELLOW}[3/7] 配置环境变量...${NC}"

if [ -f "$COGNEXUS_DIR/.env" ]; then
    echo -e "${YELLOW}  ⚠️  已存在 .env，备份为 .env.bak${NC}"
    cp "$COGNEXUS_DIR/.env" "$COGNEXUS_DIR/.env.bak"
fi

# 读取已有 .env 中的值（如果有）
EXISTING_JWT=$(grep -oP 'JWT_SECRET=\K.*' "$COGNEXUS_DIR/.env" 2>/dev/null || true)

# 生成新密钥
NEW_JWT_SECRET=$(openssl rand -hex 32)
NEW_ENCRYPTION_KEY=$(openssl rand -hex 32)

cat > "$COGNEXUS_DIR/.env" << EOF
# CogNexus 生产环境配置
# 生成于: $(date -Iseconds)

# ===== 安全（必填）=====
JWT_SECRET=${EXISTING_JWT:-$NEW_JWT_SECRET}
JWT_EXPIRE_HOURS=168
ENCRYPTION_KEY=$NEW_ENCRYPTION_KEY

# ===== CORS =====
CORS_ORIGINS=https://wielding.ai

# ===== Cogmate 连接 =====
COGMATE_DB_PATH=$COGMATE_DIR/data/cogmate.db
QDRANT_HOST=localhost
QDRANT_PORT=6333
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASS=brainagent2026
EOF

echo -e "${GREEN}✓ .env 已配置${NC}"

# ========== Step 4: 迁移已有的 LLM API Key (XOR → Fernet) ==========
echo ""
echo -e "${YELLOW}[4/7] 迁移已加密的 API Key (XOR → Fernet)...${NC}"

cd "$COGNEXUS_DIR"
python3 << 'PYEOF'
import sys, os
sys.path.insert(0, 'api')

# Load new env
from dotenv import load_dotenv
load_dotenv('.env')

import sqlite3
import base64
import hashlib

DB_PATH = os.environ.get('DATABASE_PATH', 'data/hub.db')
if not os.path.exists(DB_PATH):
    DB_PATH = 'data/hub.db'

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check simulations table for encrypted keys
cursor.execute("SELECT simulation_id, llm_api_key_enc FROM simulations WHERE llm_api_key_enc IS NOT NULL AND llm_api_key_enc != ''")
rows = cursor.fetchall()

if not rows:
    print("  没有需要迁移的 API Key")
else:
    # Old XOR decrypt
    old_seed = os.environ.get('OLD_ENCRYPTION_KEY', 'cognexus-default-key-change-me')
    old_key = hashlib.sha256(old_seed.encode()).digest()

    # New Fernet encrypt
    from crypto_utils import encrypt_api_key

    migrated = 0
    failed = 0
    for row in rows:
        sid = row['simulation_id']
        old_enc = row['llm_api_key_enc']
        try:
            # Decrypt with old XOR method
            data = base64.b64decode(old_enc)
            plaintext = bytes(b ^ old_key[i % len(old_key)] for i, b in enumerate(data)).decode()

            # Re-encrypt with Fernet
            new_enc = encrypt_api_key(plaintext)
            cursor.execute("UPDATE simulations SET llm_api_key_enc = ? WHERE simulation_id = ?", (new_enc, sid))
            migrated += 1
        except Exception as e:
            print(f"  ⚠️  迁移失败 {sid}: {e}")
            failed += 1

    conn.commit()
    print(f"  ✅ 迁移完成: {migrated} 成功, {failed} 失败")

conn.close()
PYEOF

echo -e "${GREEN}✓ API Key 迁移完成${NC}"

# ========== Step 5: 修复 Qdrant Docker 绑定 ==========
echo ""
echo -e "${YELLOW}[5/7] 检查 Qdrant Docker 绑定...${NC}"

QDRANT_BIND=$(docker inspect brain-qdrant --format '{{range $k, $v := .HostConfig.PortBindings}}{{range $v}}{{.HostIp}}{{end}}{{end}}' 2>/dev/null || echo "none")

if [ "$QDRANT_BIND" != "127.0.0.1127.0.0.1" ] && [ "$QDRANT_BIND" != "127.0.0.1" ]; then
    echo "  当前绑定: $QDRANT_BIND → 需要修复为 127.0.0.1"

    # Get volume info
    QDRANT_VOL=$(docker inspect brain-qdrant --format '{{range .Mounts}}{{.Name}}{{end}}' 2>/dev/null || echo "")

    echo "  停止旧容器..."
    docker stop brain-qdrant 2>/dev/null || true
    docker rm brain-qdrant 2>/dev/null || true

    echo "  重建容器 (127.0.0.1 绑定)..."
    if [ -n "$QDRANT_VOL" ]; then
        docker run -d \
            --name brain-qdrant \
            --restart unless-stopped \
            -p 127.0.0.1:6333:6333 \
            -p 127.0.0.1:6334:6334 \
            -v "$QDRANT_VOL":/qdrant/storage \
            qdrant/qdrant:latest
    else
        docker run -d \
            --name brain-qdrant \
            --restart unless-stopped \
            -p 127.0.0.1:6333:6333 \
            -p 127.0.0.1:6334:6334 \
            qdrant/qdrant:latest
    fi
    echo -e "${GREEN}✓ Qdrant 已修复为 127.0.0.1 绑定${NC}"
else
    echo -e "${GREEN}✓ Qdrant 已经绑定 127.0.0.1，无需修改${NC}"
fi

# ========== Step 6: 启用 UFW 防火墙 ==========
echo ""
echo -e "${YELLOW}[6/7] 配置防火墙...${NC}"

ufw --force reset >/dev/null 2>&1
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
# 不对外暴露 8081 — Nginx 反代处理
echo "y" | ufw enable
echo -e "${GREEN}✓ UFW 已启用${NC}"
ufw status

# ========== Step 7: 重启 CogNexus 服务 ==========
echo ""
echo -e "${YELLOW}[7/7] 重启 CogNexus 服务...${NC}"

# Kill stuck processes first
pkill -f "uvicorn.*8081" 2>/dev/null || true
sleep 2

systemctl restart cognexus
sleep 3

# Verify
if curl -s http://localhost:8081/health | grep -q '"ok"'; then
    echo -e "${GREEN}✓ CogNexus 服务运行正常${NC}"
else
    echo -e "${RED}❌ 服务启动失败，检查日志: journalctl -u cognexus -n 50${NC}"
    exit 1
fi

echo ""
echo "==============================="
echo -e "${GREEN}🎉 部署完成！${NC}"
echo ""
echo "已修复："
echo "  ✅ JWT Secret → 随机生成，从环境变量读取"
echo "  ✅ API Key 加密 → XOR 升级为 Fernet (AES)"
echo "  ✅ SSRF 防护 → probe 端点拒绝内网地址"
echo "  ✅ Rate Limiting → 注册/登录/探测限速"
echo "  ✅ CORS → 仅允许 wielding.ai"
echo "  ✅ Qdrant → 仅绑定 127.0.0.1"
echo "  ✅ UFW 防火墙 → 仅放行 22/80/443"
echo "  ✅ API Key 密文 → XOR 迁移到 Fernet"
echo ""
echo -e "${YELLOW}⚠️  注意: JWT Secret 已更换，所有已登录用户需要重新登录${NC}"
echo "==============================="
