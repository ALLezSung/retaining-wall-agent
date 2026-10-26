"""Composition root: choose local adapters and inject application-owned ports."""
from pathlib import Path
from .config import ProjectConfig
from .llm.compatible import OpenAICompatibleModel


def configured_model(config):
    return OpenAICompatibleModel(config)


def conversation_store(path):
    from .design_agent.infrastructure.sqlite_conversations import SQLiteConversationStore
    return SQLiteConversationStore(path)


def application_ports(config, *, db_path=None, conversation_store=None):
    from .design_agent.ports import ApplicationPorts
    from .design_agent.infrastructure.catalog import LocalSkillCatalog
    from .design_agent.infrastructure.executor import ScriptExecutor
    from .design_agent.infrastructure.files import LocalArtifactStore
    from .design_agent.infrastructure.sqlite_conversations import SQLiteConversationStore
    from .design_agent.infrastructure.sqlite_resources import SQLiteResourceStore

    chats = db_path if db_path is not None else config.storage.conversations_database
    resources = db_path if db_path is not None else config.storage.cases_database
    workspace = Path(db_path).parent / "workspace" if db_path is not None else config.skills.workspace
    return ApplicationPorts(
        conversations=conversation_store if conversation_store is not None else SQLiteConversationStore(chats),
        catalog=LocalSkillCatalog(list(config.skills.directories)),
        executor=ScriptExecutor(),
        resources=SQLiteResourceStore(resources),
        artifacts=LocalArtifactStore(workspace),
    )


def create_agent(model=None, *, db_path=None, event_sink=None, max_rounds=None,
                 config=None, conversation_store=None, ports=None):
    from .design_agent import DesignAgent
    config = config if config is not None else ProjectConfig.load()
    if ports is not None and (db_path is not None or conversation_store is not None):
        raise ValueError("注入完整 ports 时，不再指定默认存储配置覆盖。")
    ports = ports if ports is not None else application_ports(
        config, db_path=db_path, conversation_store=conversation_store)
    return DesignAgent(model if model is not None else configured_model(config.llm),
                       config=config, ports=ports, event_sink=event_sink, max_rounds=max_rounds)
