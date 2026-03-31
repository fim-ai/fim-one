"""Model abstraction layer for LLM providers."""

from .base import REASONING_INHERIT, BaseLLM
from .config import ModelConfig, create_registry_from_configs
from .fallback import FallbackLLM
from .openai_compatible import OpenAICompatibleLLM, close_shared_http_client
from .rate_limit import RateLimitConfig, TokenBucketRateLimiter
from .registry import ModelRegistry
from .retry import RetryConfig
from .structured import StructuredCallResult, StructuredOutputError, structured_llm_call
from .types import ChatMessage, LLMResult, StreamChunk, ToolCallRequest
from .usage import UsageSummary, UsageTracker

__all__ = [
    "BaseLLM",
    "FallbackLLM",
    "REASONING_INHERIT",
    "ChatMessage",
    "LLMResult",
    "ModelConfig",
    "ModelRegistry",
    "OpenAICompatibleLLM",
    "close_shared_http_client",
    "RateLimitConfig",
    "RetryConfig",
    "StreamChunk",
    "StructuredCallResult",
    "StructuredOutputError",
    "TokenBucketRateLimiter",
    "ToolCallRequest",
    "UsageSummary",
    "UsageTracker",
    "create_registry_from_configs",
    "structured_llm_call",
]
