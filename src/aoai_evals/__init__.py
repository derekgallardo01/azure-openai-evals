"""Azure OpenAI deployment readiness + eval kit.

Default backend is a deterministic stub so the kit runs without an Azure
subscription. Set AOAI_BACKEND=azure (with AZURE_OPENAI_API_KEY +
AZURE_OPENAI_ENDPOINT) to route through the real Azure OpenAI service.
"""
__version__ = "1.0.0"
