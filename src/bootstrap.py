"""Composition root. Concrete adapters point inward to application contracts."""
from .config import ProjectConfig
from .llm.compatible import OpenAICompatibleModel
from .persistence.sqlite import SQLiteConversationStore


def configured_model(config):
    return OpenAICompatibleModel(config)


def conversation_store(path):
    return SQLiteConversationStore(path)


def create_agent():
    from .design_agent import DesignAgent
    config = ProjectConfig.load()
    return DesignAgent(config=config, model=configured_model(config.llm),
                       conversation_store=conversation_store(config.storage.conversations_database))
