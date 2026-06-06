"""密码加解密工具。

加密：encrypt_password(明文 str) -> 密文 bytes
解密：decrypt_password(密文 bytes) -> 明文 str

密钥从 config.ENCRYPTION_KEY 读取（最终来源是 .env 的 ENCRYPTION_KEY）。
"""

from cryptography.fernet import Fernet, InvalidToken

import config


ENCRYPTION_KEY_MISSING_MESSAGE = (
    "未配置 ENCRYPTION_KEY，请在项目根目录 .env 中设置。"
    "生成方式：python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
)
DECRYPT_FAILED_MESSAGE = "密码解密失败：密钥不匹配或密文已损坏"


_cipher: Fernet | None = None


def _get_cipher() -> Fernet:
    """懒加载 Fernet 实例并缓存。首次调用时校验密钥是否存在。"""
    global _cipher
    if _cipher is None:
        key = config.ENCRYPTION_KEY
        if not key:
            raise RuntimeError(ENCRYPTION_KEY_MISSING_MESSAGE)
        # Fernet 接受 bytes 也接受 str（自动 encode）
        _cipher = Fernet(key.encode() if isinstance(key, str) else key)
    return _cipher


def reset_cipher() -> None:
    """清空缓存，主要给测试用（切换密钥时重置）。"""
    global _cipher
    _cipher = None


def encrypt_password(plaintext: str) -> bytes:
    """加密明文密码。空字符串返回 b""，不加密。"""
    if not plaintext:
        return b""
    return _get_cipher().encrypt(plaintext.encode("utf-8"))


def decrypt_password(ciphertext: bytes) -> str:
    """解密密文。空 bytes 返回 ""。密钥不对或密文损坏抛 ValueError。"""
    if not ciphertext:
        return ""
    try:
        return _get_cipher().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(DECRYPT_FAILED_MESSAGE) from exc
