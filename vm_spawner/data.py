from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypedDict


class Provider(Enum):
    Hetzner = "hetzner"

    @staticmethod
    def from_str(label: str) -> "Provider":
        if label in Provider._value2member_map_:
            return Provider(Provider._value2member_map_[label])
        msg = f"Unknown provider: {label}"
        raise ValueError(msg)


class ArgMachine(TypedDict):
    name: str
    arch: str
    os_image: str | None


class TrMachine(TypedDict):
    name: str
    location: str | None
    server_type: str
    os_image: str
    arch: str
    ipv4: str | None
    ipv6: str | None
    internal_ipv6: str | None
    provider: Provider


@dataclass
class SSHKeyPair:
    private: Path
    public: Path


@dataclass
class Config:
    debug: bool
    data_dir: Path
    cache_dir: Path
    tr_dir: Path
    clan_dir: Path
    ssh_keys: list[SSHKeyPair]
