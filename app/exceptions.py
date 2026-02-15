"""
Custom exception classes for the LLM Gateway.
"""


class BudgetExceededException(Exception):
    """Raised when budget limit is reached during streaming."""

    def __init__(self, message: str = "Budget limit reached during generation"):
        self.message = message
        super().__init__(self.message)


class ContextLengthExceededException(Exception):
    """Raised when request exceeds model context window."""

    def __init__(self, message: str = "Request exceeds model context window", details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)
