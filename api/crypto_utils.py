"""
API Key 加密工具
使用 Fernet 对称加密（AES-128-CBC + HMAC-SHA256）
"""
import os
import base64
import hashlib
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    """从环境变量获取加密密钥，派生为 Fernet key"""
    seed = os.environ.get('ENCRYPTION_KEY')
    if not seed:
        raise RuntimeError(
            "❌ ENCRYPTION_KEY 环境变量未设置！"
            "请在 .env 中配置: ENCRYPTION_KEY=$(openssl rand -hex 32)"
        )
    # 用 SHA256 派生固定长度的 key → base64 编码为 Fernet 要求的 32 字节
    derived = hashlib.sha256(seed.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_api_key(plaintext: str) -> str:
    """Fernet 加密（AES-128-CBC + HMAC，含时间戳）"""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Fernet 解密"""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


# ==================== 迁移工具 ====================

def migrate_xor_to_fernet(old_ciphertext: str, old_seed: str) -> str:
    """将旧 XOR 加密的密文迁移到 Fernet（一次性工具）"""
    # 先用旧方式解密
    old_key = hashlib.sha256(old_seed.encode()).digest()
    data = base64.b64decode(old_ciphertext)
    plaintext = bytes(b ^ old_key[i % len(old_key)] for i, b in enumerate(data)).decode()
    # 再用新方式加密
    return encrypt_api_key(plaintext)
