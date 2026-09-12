"""Load `.decision-agent.toml` with sensible defaults and env overrides."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DEFAULT_CONFIG_FILENAME = ".decision-agent.toml"

DEFAULTS = {
    "decisions_dir": "decisions",
    "model": "sonnet",
    "severity_threshold": "medium",
    "max_diff_bytes": 400_000,
    "propose_decisions": True,
    "label": "decision-conflict",
    "ignore_paths": ["**/*.lock", "dist/**", "**/*.min.js"],
}

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Config:
    decisions_dir: str = DEFAULTS["decisions_dir"]
    model: str = DEFAULTS["model"]
    severity_threshold: str = DEFAULTS["severity_threshold"]
    max_diff_bytes: int = DEFAULTS["max_diff_bytes"]
    propose_decisions: bool = DEFAULTS["propose_decisions"]
    label: str = DEFAULTS["label"]
    ignore_paths: list[str] = field(default_factory=lambda: list(DEFAULTS["ignore_paths"]))

    repo_root: Path = field(default_factory=Path.cwd)

    def decisions_path(self) -> Path:
        return self.repo_root / self.decisions_dir

    def severity_rank(self, severity: str) -> int:
        return SEVERITY_ORDER.get(severity, 0)

    def meets_threshold(self, severity: str) -> bool:
        return self.severity_rank(severity) >= self.severity_rank(self.severity_threshold)


def load_config(repo_root: Path | None = None, path: Path | None = None) -> Config:
    """Load config from `path` (or `<repo_root>/.decision-agent.toml`), falling
    back to defaults for anything missing. Unknown keys are ignored."""
    root = repo_root or Path.cwd()
    config_path = path or (root / DEFAULT_CONFIG_FILENAME)

    data: dict = {}
    if config_path.is_file():
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)

    kwargs = {}
    for key, default in DEFAULTS.items():
        kwargs[key] = data.get(key, default)

    cfg = Config(repo_root=root, **kwargs)

    # Env overrides for the handful of settings that make sense to override in CI.
    if env_model := os.environ.get("DECISION_AGENT_MODEL"):
        cfg.model = env_model
    if env_threshold := os.environ.get("DECISION_AGENT_SEVERITY_THRESHOLD"):
        cfg.severity_threshold = env_threshold

    return cfg
