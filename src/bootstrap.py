"""Composition root. Concrete adapters point inward to application contracts."""
from .config import ProjectConfig
from .llm.compatible import OpenAICompatibleModel


def configured_model(config):
    return OpenAICompatibleModel(config)


def conversation_store(path):
    # Import lazily so importing the composition root does not initialize the
    # design application package while it is still being assembled.
    from .design_agent.persistence.sqlite import SQLiteConversationStore
    return SQLiteConversationStore(path)


def create_agent():
    from .design_agent import DesignAgent
    config = ProjectConfig.load()
    return DesignAgent(config=config, model=configured_model(config.llm),
                       conversation_store=conversation_store(config.storage.conversations_database))
