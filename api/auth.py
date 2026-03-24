"""
Hub Authentication Module
"""
import os
import hashlib
import secrets
import jwt
from datetime import datetime, timedelta
from typing import Optional

# JWT 配置 — 强制从环境变量读取
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "❌ JWT_SECRET 环境变量未设置！"
        "请在 .env 中配置: JWT_SECRET=$(openssl rand -hex 32)"
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", 168))  # 默认 7 天


def hash_password(password: str) -> str:
    """哈希密码"""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256', 
        password.encode(), 
        salt.encode(), 
        100000
    )
    return f"{salt}:{pwd_hash.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    try:
        salt, stored_hash = password_hash.split(':')
        pwd_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            salt.encode(),
            100000
        )
        return pwd_hash.hex() == stored_hash
    except:
        return False


def create_token(user_id: str, username: str) -> str:
    """创建 JWT Token"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT Token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def generate_agent_token() -> str:
    """生成 Agent 访问 Token"""
    return f"tok_{secrets.token_urlsafe(32)}"
