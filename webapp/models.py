from dataclasses import dataclass, field
from typing import List


@dataclass
class RGBStrip:
    """Configuration for an addressable RGB strip."""
    index: int
    name: str


@dataclass
class WhiteChannel:
    """Configuration for a PWM white channel."""
    index: int
    name: str


@dataclass
class Sensor:
    """Configuration for a sensor attached to the node."""
    type: str
    name: str


@dataclass
class NodeConfig:
    """Configuration describing devices attached to an UltraNode."""
    node_id: str
    rgb_strips: List[RGBStrip] = field(default_factory=list)
    white_channels: List[WhiteChannel] = field(default_factory=list)
    sensors: List[Sensor] = field(default_factory=list)
