"""Application errors shared by ports, policies and their adapters."""


class CapabilityError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ConversationStorageError(RuntimeError):
    code = "PERSISTENCE_FAILED"


class ConversationConflict(ConversationStorageError):
    code = "STALE_STATE"
