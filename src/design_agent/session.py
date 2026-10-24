"""Single authoritative design state. Agent transcript is held separately."""
from dataclasses import dataclass, field


@dataclass
class DesignSession:
    parameters: dict[str, float] = field(default_factory=dict)
    parameter_sources: dict[str, str] = field(default_factory=dict)
    wall_shape: str | None = None
    current_result: dict | None = None
    pending_options: dict | None = None
    selected_standards: list[str] = field(default_factory=lambda: ["GB 50330-2013", "GB 55003-2021"])
