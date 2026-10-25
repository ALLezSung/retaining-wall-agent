"""Backward-compatible DeepSeek adapter entry point."""
from urllib.request import urlopen
from .compatible import OpenAICompatibleModel, provider_message


class DeepSeekModel(OpenAICompatibleModel):
    def _open(self, request, timeout):
        return urlopen(request, timeout=timeout)
