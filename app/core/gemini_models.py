"""Bounded model failover without SDK retries multiplying request latency."""
import re
from google.genai import types


def candidates(value):
    result = []
    for model in value.split(','):
        model = model.strip()
        if re.fullmatch(r'gemini-[a-zA-Z0-9.-]+', model) and model not in result:
            result.append(model)
    return result[:2]


def http_options(timeout=10000):
    return types.HttpOptions(timeout=timeout, retry_options=types.HttpRetryOptions(attempts=1))


def thinking(model):
    # Both current stable text models support low; older configured models
    # retain their own defaults instead of receiving unsupported parameters.
    if model.startswith(('gemini-3.', 'gemini-4-')):
        return types.ThinkingConfig(thinking_level='low')
    return None


def failure_code(error):
    # Provider messages can embed request details: log only a bounded code.
    value = getattr(error, 'code', None)
    return str(value) if isinstance(value, int) else type(error).__name__
