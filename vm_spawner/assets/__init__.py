from pathlib import Path

from vm_spawner.data import Provider


def get_cloud_asset(provider: Provider | str, asset_name: str) -> Path:
    curr = Path(__file__).parent
    if isinstance(provider, str):
        asset = curr / provider / asset_name
    else:
        asset = curr / provider.value / asset_name
    if not asset.exists():
        msg = f"{asset} does not exist"
        raise ValueError(msg)
    return asset


def get_script_asset(asset_name: str) -> Path:
    curr = Path(__file__).parent
    asset = curr / "scripts" / asset_name
    if not asset.exists():
        msg = f"{asset} does not exist"
        raise ValueError(msg)
    return asset
