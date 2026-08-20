"""Pydantic models shared across the app."""
from typing import Optional

from pydantic import BaseModel, Field


class PlayerConfig(BaseModel):
    """Persisted configuration for a single virtual snapclient player."""

    name: str = Field(..., description="Unique player name, shown as hostID in Snapserver/Music Assistant")
    device: str = Field(..., description="ALSA device string, e.g. hw:1,0 or plughw:1,0")
    snapserver_host: Optional[str] = Field(
        default=None, description="Overrides the default Snapserver host for this player"
    )
    snapserver_port: Optional[int] = Field(
        default=None, description="Overrides the default Snapserver port for this player"
    )
    buffer_time_ms: int = Field(default=80, ge=10, description="ALSA player buffer_time in ms (snapclient default: 80)")
    fragments: int = Field(default=4, ge=2, description="ALSA player fragment count (snapclient default: 4)")
    latency_ms: int = Field(
        default=0, description="Extra output latency compensation in ms (amp/speaker delay), passed as --latency"
    )
    sampleformat: Optional[str] = Field(
        default=None, description="Optional forced sample format, e.g. 48000:16:2"
    )
    extra_args: str = Field(default="", description="Additional raw snapclient CLI arguments")
    enabled: bool = Field(default=True, description="Auto-start this player when the container starts")


class PlayerUpdate(BaseModel):
    """Partial update payload; only set fields are applied."""

    device: Optional[str] = None
    snapserver_host: Optional[str] = None
    snapserver_port: Optional[int] = None
    buffer_time_ms: Optional[int] = None
    fragments: Optional[int] = None
    latency_ms: Optional[int] = None
    sampleformat: Optional[str] = None
    extra_args: Optional[str] = None
    enabled: Optional[bool] = None


class Settings(BaseModel):
    default_snapserver_host: str = ""
    default_snapserver_port: int = 1704
