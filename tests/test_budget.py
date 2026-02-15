"""
Unit tests for budget calculation logic (pure functions only).
"""

from decimal import Decimal

import pytest

from app.models.schemas import ApiKey, ModelConfig


class TestBudgetEstimation:
    """Test budget estimation logic without Redis/DB."""

    @pytest.fixture
    def model(self):
        return ModelConfig(
            id="gpt-4o",
            litellm_name="openai/gpt-4o",
            provider="openai",
            input_cost=Decimal("3000"),    # ¥3000 per 1M input tokens
            output_cost=Decimal("12000"),  # ¥12000 per 1M output tokens
            context_window=128_000,
            max_output_tokens=4096,
        )

    def test_cost_estimation_formula(self, model):
        """Verify the pessimistic cost estimation formula from the spec."""
        max_tokens = 1000
        estimated_cost = (
            (max_tokens / 1_000_000) * float(model.input_cost)
            + (max_tokens / 1_000_000) * float(model.output_cost)
        )
        # (1000/1M * 3000) + (1000/1M * 12000) = 3.0 + 12.0 = 15.0
        assert abs(estimated_cost - 15.0) < 0.001

    def test_no_budget_means_unlimited(self):
        """API key without budget_monthly should not be limited."""
        api_key = ApiKey(
            id="00000000-0000-0000-0000-000000000001",
            user_oid="user-1",
            hashed_key="a" * 64,
            salt="b" * 32,
            display_prefix="sk-gate-abc...",
            budget_monthly=None,
        )
        assert api_key.budget_monthly is None

    def test_budget_comparison(self):
        api_key = ApiKey(
            id="00000000-0000-0000-0000-000000000002",
            user_oid="user-1",
            hashed_key="a" * 64,
            salt="b" * 32,
            display_prefix="sk-gate-abc...",
            budget_monthly=Decimal("100.00"),
            usage_current_month=Decimal("95.00"),
        )
        remaining = float(api_key.budget_monthly) - float(api_key.usage_current_month)
        assert remaining == 5.0
