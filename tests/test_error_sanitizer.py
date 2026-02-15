"""
Unit tests for error sanitization.
"""

from app.services.error_sanitizer import (
    classify_and_sanitize_error,
    sanitize_error_message,
)


class TestSanitizeErrorMessage:
    def test_truncates_long_message(self):
        long_msg = "x" * 500
        result = sanitize_error_message(long_msg, max_length=100)
        assert len(result) < 200
        assert "(truncated)" in result

    def test_removes_unix_file_paths(self):
        msg = "Error in /home/user/app/main.py line 42"
        result = sanitize_error_message(msg)
        assert "/home/user" not in result
        assert "*.py" in result

    def test_removes_windows_file_paths(self):
        msg = "Error in C:\\Users\\app\\main.py line 42"
        result = sanitize_error_message(msg)
        assert "C:\\Users" not in result

    def test_removes_ip_addresses(self):
        msg = "Connection refused to 192.168.1.100"
        result = sanitize_error_message(msg)
        assert "192.168.1.100" not in result
        assert "[IP]" in result

    def test_removes_bearer_tokens(self):
        msg = "Auth failed with Bearer sk-1234567890abcdef"
        result = sanitize_error_message(msg)
        assert "sk-1234567890" not in result
        assert "[REDACTED]" in result

    def test_removes_api_keys(self):
        msg = "Invalid key sk-gate-abcdef12345"
        result = sanitize_error_message(msg)
        assert "sk-gate-abcdef" not in result

    def test_short_message_unchanged(self):
        msg = "Simple error"
        result = sanitize_error_message(msg)
        assert result == msg


class TestClassifyAndSanitizeError:
    def test_oom_error(self):
        code, msg = classify_and_sanitize_error(
            RuntimeError("CUDA out of memory")
        )
        assert code == "oom_error"
        assert "memory" in msg.lower()

    def test_timeout_error(self):
        code, msg = classify_and_sanitize_error(
            TimeoutError("Request timeout after 120s")
        )
        assert code == "timeout"

    def test_rate_limit_error(self):
        code, msg = classify_and_sanitize_error(
            Exception("Rate limit exceeded")
        )
        assert code == "rate_limit"

    def test_gpu_error(self):
        code, msg = classify_and_sanitize_error(
            Exception("GPU memory allocation failed")
        )
        assert code == "gpu_error"

    def test_model_not_found(self):
        code, msg = classify_and_sanitize_error(
            Exception("Model not found: llama-70b")
        )
        assert code == "model_not_loaded"

    def test_generic_error_sanitized(self):
        code, msg = classify_and_sanitize_error(
            Exception("Error in /app/internal/handler.py: DB password=secret123")
        )
        assert code == "provider_error"
        assert "/app/internal" not in msg
