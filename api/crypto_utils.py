"""
API Key 加密工具
轻量级 XOR + base64 加密（非生产级安全）
"""
import os
import base64
import hashlib


def _get_key() -> bytes:
    """从环境变量或固定文件读取密钥种子"""
    seed = os.environ.get('ENCRYPTION_KEY', 'cognexus-default-key-change-me')
    return hashlib.sha256(seed.encode()).digest()


def encrypt_api_key(plaintext: str) -> str:
    """XOR 加密 + base64（轻量级，非生产级安全）"""
    key = _get_key()
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(plaintext.encode()))
    return base64.b64encode(encrypted).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """XOR 解密"""
    key = _get_key()
    data = base64.b64decode(ciphertext)
    decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return decrypted.decode()
