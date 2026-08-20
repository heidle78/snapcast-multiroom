"""YAML-backed persistence for player configs and global settings."""
from pathlib import Path

import yaml

from models import PlayerConfig, Settings


class ConfigStore:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config_path.mkdir(parents=True, exist_ok=True)
        self.players_file = self.config_path / "players.yaml"
        self.settings_file = self.config_path / "settings.yaml"

    def load_players(self) -> list[PlayerConfig]:
        if not self.players_file.exists():
            return []
        data = yaml.safe_load(self.players_file.read_text()) or []
        return [PlayerConfig(**item) for item in data]

    def save_players(self, players: list[PlayerConfig]) -> None:
        data = [p.model_dump() for p in players]
        self.players_file.write_text(yaml.safe_dump(data, sort_keys=False))

    def load_settings(self) -> Settings:
        if not self.settings_file.exists():
            return Settings()
        data = yaml.safe_load(self.settings_file.read_text()) or {}
        return Settings(**data)

    def save_settings(self, settings: Settings) -> None:
        self.settings_file.write_text(yaml.safe_dump(settings.model_dump(), sort_keys=False))
