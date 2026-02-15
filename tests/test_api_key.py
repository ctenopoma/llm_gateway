"""
Unit tests for API key generation & verification.
"""

from app.services.api_key import generate_api_key, verify_api_key_fast


class TestGenerateApiKey:
    def test_returns_four_values(self):
        result = generate_api_key()
        assert len(result) == 4

    def test_plaintext_key_format(self):
        plaintext, _, _, _ = generate_api_key()
        assert plaintext.startswith("sk-gate-")
        assert len(plaintext) > 20

    def test_hashed_key_is_64_hex(self):
        _, hashed, _, _ = generate_api_key()
        assert len(hashed) == 64
        int(hashed, 16)  # should not raise

    def test_salt_is_32_hex(self):
        _, _, salt, _ = generate_api_key()
        assert len(salt) == 32
        int(salt, 16)

    def test_display_prefix(self):
        plaintext, _, _, prefix = generate_api_key()
        assert prefix == plaintext[:15] + "..."

    def test_unique_keys(self):
        keys = {generate_api_key()[0] for _ in range(10)}
        assert len(keys) == 10


class TestVerifyApiKeyFast:
    def test_correct_key_returns_true(self):
        plaintext, hashed, salt, _ = generate_api_key()
        assert verify_api_key_fast(plaintext, hashed, salt) is True

    def test_wrong_key_returns_false(self):
        _, hashed, salt, _ = generate_api_key()
        assert verify_api_key_fast("sk-gate-wrong", hashed, salt) is False

    def test_wrong_salt_returns_false(self):
        plaintext, hashed, _, _ = generate_api_key()
        assert verify_api_key_fast(plaintext, hashed, "0" * 32) is False

    def test_empty_key_returns_false(self):
        _, hashed, salt, _ = generate_api_key()
        assert verify_api_key_fast("", hashed, salt) is False
