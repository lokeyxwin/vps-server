import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet

import config
from toolbox.security import (
    encrypt_password,
    decrypt_password,
    reset_cipher,
    ENCRYPTION_KEY_MISSING_MESSAGE,
    DECRYPT_FAILED_MESSAGE,
)


class TestEncryptDecryptRoundtrip(unittest.TestCase):
    def setUp(self):
        reset_cipher()  # 每个测试独立的 cipher 状态

    def test_roundtrip_recovers_plaintext(self):
        ciphertext = encrypt_password("Tyi#WGmP7yyFkk22")
        self.assertIsInstance(ciphertext, bytes)
        self.assertNotIn(b"Tyi#WGmP7yyFkk22", ciphertext)  # 不应该明文出现在密文里
        self.assertEqual(decrypt_password(ciphertext), "Tyi#WGmP7yyFkk22")

    def test_each_encryption_produces_different_ciphertext(self):
        """Fernet 自带随机 IV，相同明文每次加密结果不同——这是安全特性。"""
        c1 = encrypt_password("samepassword")
        c2 = encrypt_password("samepassword")
        self.assertNotEqual(c1, c2)
        self.assertEqual(decrypt_password(c1), "samepassword")
        self.assertEqual(decrypt_password(c2), "samepassword")

    def test_handles_unicode_password(self):
        ciphertext = encrypt_password("密码🔑测试")
        self.assertEqual(decrypt_password(ciphertext), "密码🔑测试")

    def test_empty_string_returns_empty_bytes(self):
        self.assertEqual(encrypt_password(""), b"")
        self.assertEqual(decrypt_password(b""), "")


class TestKeyValidation(unittest.TestCase):
    def setUp(self):
        reset_cipher()

    def tearDown(self):
        reset_cipher()

    @patch.object(config, "ENCRYPTION_KEY", "")
    def test_missing_key_raises_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            encrypt_password("anything")
        self.assertEqual(str(ctx.exception), ENCRYPTION_KEY_MISSING_MESSAGE)

    def test_wrong_key_cannot_decrypt(self):
        # 用 A 钥匙加密
        ciphertext = encrypt_password("secret123")

        # 换 B 钥匙再解
        reset_cipher()
        with patch.object(config, "ENCRYPTION_KEY", Fernet.generate_key().decode()):
            with self.assertRaises(ValueError) as ctx:
                decrypt_password(ciphertext)
            self.assertEqual(str(ctx.exception), DECRYPT_FAILED_MESSAGE)

    def test_corrupted_ciphertext_raises_value_error(self):
        with self.assertRaises(ValueError):
            decrypt_password(b"not_a_valid_ciphertext")


if __name__ == "__main__":
    unittest.main()
