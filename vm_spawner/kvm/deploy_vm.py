#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import libvirt

from .connect import connect_libvirt
from .create import (
    create_linked_clone_disk,
    ensure_volume_from_file,
    get_or_create_pool,
)
from .install import install_domain_with_virt_install
from .network import get_domain_ip_from_network
from .remote import run_remote_command

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


@dataclass
class DeployVMConfig:
    remote_user_host: str
    libvirt_uri: str
    remote_tmp_dir: Path
    libvirt_remote_uri: str  # URI used *by virt-install on the remote host*
    pool_name: str
    pool_type: str
    pool_path: Path
    base_image_url: str
    base_image_checksum: str | None
    base_image_vol_name: str  # Name for the base image volume in the pool
    base_image_format: str
    local_download_dir: Path
    domain_name: str
    memory_mb: int
    vcpu: int
    primary_network: str
    isolated_network: str | None
    os_variant: str
    user_data: Path
    network_config: Path
    virt_install_extra_args: list[str] | None = None


@dataclass
class VMConfig:
    name: str
    ip: str


def deploy_vm(cfg: DeployVMConfig, ssh_key: Path | None) -> VMConfig:
    """
    Business logic for deploying a VM based on the configuration.

    Returns:
        VMConfig containing the VM name and IP address.
    Raises:
        Exception: If any stage of the deployment fails (connection, pool, volume, install, IP retrieval).
                   The specific exception type will indicate the failure point.
    """
    conn: libvirt.virConnect | None = None
    try:
        # --- Setup ---
        log.info(f"Starting deployment for VM: {cfg.domain_name}")
        local_base_image_path = cfg.local_download_dir / Path(cfg.base_image_url).name

        # 1. Connect to Libvirt
        conn = connect_libvirt(cfg.libvirt_uri)

        # 2. Get/Create Storage Pool
        # get_or_create_pool raises exceptions on failure
        storage_pool = get_or_create_pool(
            conn,
            cfg.remote_user_host,
            cfg.pool_name,
            cfg.pool_type,
            cfg.pool_path,
            ssh_key,
        )

        # 3. Ensure Base Image Volume exists in Pool (Download locally, then upload if needed)
        # ensure_volume_from_file raises exceptions on failure
        log.info(f"Ensuring base volume '{cfg.base_image_vol_name}' exists...")
        base_volume = ensure_volume_from_file(
            conn,
            storage_pool,
            cfg.remote_user_host,
            cfg.base_image_vol_name,
            local_base_image_path,
            cfg.base_image_format,
            cfg.base_image_url,
            cfg.base_image_checksum,
            ssh_key,
        )

        # 4. Handle disk setup based on image format
        is_iso = cfg.base_image_format.lower() == "iso"

        if is_iso:
            # For ISO: use the ISO directly as CDROM, create a blank disk for OS installation
            log.info(f"Using ISO '{cfg.base_image_vol_name}' as installation media...")
            iso_volume_name = cfg.base_image_vol_name
            # Create a blank disk for the VM
            from .create import create_blank_disk
            blank_disk_path = create_blank_disk(
                storage_pool=storage_pool,
                remote_host=cfg.remote_user_host,
                disk_name=cfg.domain_name,
                disk_size_gb=20,  # Default 20GB
                ssh_key=ssh_key,
            )
            disk_volume_name = blank_disk_path.name
            cdrom_volume_name = iso_volume_name
        else:
            # For disk images (qcow2): create linked clone as before
            log.info(f"Creating linked clone for VM '{cfg.domain_name}'...")
            cloned_disk_path = create_linked_clone_disk(
                storage_pool=storage_pool,
                remote_host=cfg.remote_user_host,
                base_volume=base_volume,
                clone_img_name=cfg.domain_name,
                ssh_key=ssh_key,
            )
            disk_volume_name = cloned_disk_path.name
            cdrom_volume_name = None

        # 5. Create Domain using virt-install
        # install_domain_with_virt_install raises exceptions on failure
        log.info(
            f"Installing domain '{cfg.domain_name}'..."
        )
        install_domain_with_virt_install(
            conn=conn,
            name=cfg.domain_name,
            memory_mb=cfg.memory_mb,
            vcpu=cfg.vcpu,
            disk_volume_name=disk_volume_name,
            cdrom_volume_name=cdrom_volume_name,
            pool_name=storage_pool.name(),
            primary_network=cfg.primary_network,
            isolated_network=cfg.isolated_network,
            os_variant=cfg.os_variant,
            user_data_path=cfg.user_data,
            network_config_path=cfg.network_config,
            remote_user_host=cfg.remote_user_host,
            remote_tmp_dir=cfg.remote_tmp_dir,
            ssh_key=ssh_key,
            libvirt_system_uri=cfg.libvirt_remote_uri,
            extra_virt_install_args=cfg.virt_install_extra_args,
        )

        # --- Post-Install ---
        # 6. Get VM IP Address
        log.info(
            f"Retrieving IP address for domain '{cfg.domain_name}' on network '{cfg.isolated_network}'..."
        )
        # Assuming isolated_network is the one providing the primary routable IP
        target_network = (
            cfg.isolated_network if cfg.isolated_network else cfg.primary_network
        )
        if not target_network:
            msg = "No suitable network specified (isolated or primary) to fetch IP address."
            raise RuntimeError(msg)

        ip = get_domain_ip_from_network(
            conn=conn,
            domain_name=cfg.domain_name,
            network_name=target_network,
            verbose=True,
        )
        if not ip:
            # If get_domain_ip_from_network doesn't raise but returns None/empty
            msg = f"Failed to determine IP address for VM '{cfg.domain_name}' on network '{target_network}' after timeout."
            log.error(msg)
            raise RuntimeError(msg)

        log.info(f"Successfully deployed VM '{cfg.domain_name}' with IP: {ip}")
        return VMConfig(name=cfg.domain_name, ip=ip)

    except Exception as e:
        log.error(f"VM deployment failed for '{cfg.domain_name}': {e}", exc_info=True)
        # Optionally, add cleanup logic here if needed (e.g., attempt to delete VM/disk on failure)
        # Be careful not to mask the original error
        raise  # Re-raise the exception that caused the failure
    finally:
        # Always try to close the connection
        if conn:
            try:
                conn.close()
                log.info("Disconnected from libvirt.")
            except libvirt.libvirtError as close_e:
                # Log error but don't raise, as the primary operation might have succeeded/failed already
                log.warning(
                    f"Error during libvirt disconnect: {close_e}", exc_info=False
                )


def create_remote_tmp_dir(remote_user_host: str, ssh_key: Path | None) -> Path:
    """Creates a temporary directory on the remote host."""
    log.info(f"Creating temporary directory on {remote_user_host}...")
    # run_remote_command raises RemoteCommandError on failure
    remote_tmp_path_str = run_remote_command(
        remote_user_host,
        ["mktemp", "-d", "/tmp/vm_spawner.XXXXXXXX"],
        ssh_key=ssh_key,
    ).stdout
    log.info(f"Remote temporary directory created: {remote_tmp_path_str}")
    return Path(remote_tmp_path_str)


from vm_spawner.assets import get_cloud_asset


def deploy_vm_auto(host: str, ssh_key: Path | None) -> VMConfig:
    """High-level function to deploy a VM with default settings."""
    vm_name = f"nixos-{uuid4()}"
    log.info(f"Starting automatic deployment for new VM: {vm_name}")

    # Get base image from environment variable
    base_image = os.environ.get("CLAN_BASE_IMAGE")
    if not base_image:
        msg = "Environment variable CLAN_BASE_IMAGE is not set. Please set it to the path or URL of the ISO image."
        log.error(msg)
        raise ValueError(msg)

    log.info(f"Using base image from CLAN_BASE_IMAGE: {base_image}")

    # Derive volume name and format from the image path/URL
    base_image_path = Path(base_image)
    base_image_vol_name = base_image_path.name

    # Detect image format from file extension
    suffix = base_image_path.suffix.lower().lstrip(".")
    if suffix in ("qcow2", "qcow"):
        base_image_format = "qcow2"
    elif suffix == "iso":
        base_image_format = "iso"
    elif suffix == "raw":
        base_image_format = "raw"
    else:
        log.warning(
            f"Unknown image format '{suffix}', defaulting to 'raw'. "
            "Supported formats: qcow2, iso, raw"
        )
        base_image_format = "raw"

    log.info(f"Detected image format: {base_image_format}")

    # Optional checksum from environment variable
    base_image_checksum = os.environ.get("CLAN_BASE_IMAGE_CHECKSUM")
    if base_image_checksum:
        log.info(f"Using checksum: {base_image_checksum}")
    else:
        log.warning("CLAN_BASE_IMAGE_CHECKSUM not set, skipping checksum verification")

    # Ensure local assets can be retrieved
    try:
        default_user_data = get_cloud_asset("kvm", "cloud_init.cfg")
        default_network_config = get_cloud_asset("kvm", "network_config.cfg")
    except Exception as asset_e:
        log.error(
            f"Failed to retrieve local cloud-init assets: {asset_e}", exc_info=True
        )
        msg = "Could not load necessary cloud-init asset files."
        raise RuntimeError(msg) from asset_e

    # Use a temporary directory for local downloads
    with TemporaryDirectory() as local_tmp_dir_str:
        local_tmp_dir = Path(local_tmp_dir_str)
        remote_tmp_dir: Path | None = None
        try:
            # Create remote temp dir *before* deploy_vm call
            remote_tmp_dir = create_remote_tmp_dir(host, ssh_key)

            if ssh_key:
                # Use SSH URI for remote connection
                libvirt_uri = f"qemu+ssh://{host}/system?keyfile={ssh_key}"
            else:
                libvirt_uri = f"qemu+ssh://{host}/system"

            cfg = DeployVMConfig(
                remote_user_host=host,
                libvirt_uri=libvirt_uri,
                remote_tmp_dir=remote_tmp_dir,
                libvirt_remote_uri="qemu:///system",  # URI for virt-install *on* the remote host
                pool_name="nixos_pool_py",
                pool_type="dir",
                pool_path=Path("/var/lib/libvirt/images/nixos-pool-py"),
                base_image_url=base_image,
                base_image_checksum=base_image_checksum,
                base_image_vol_name=base_image_vol_name,
                base_image_format=base_image_format,
                local_download_dir=local_tmp_dir,
                domain_name=vm_name,
                memory_mb=6144,
                vcpu=4,
                primary_network="default",  # Assumes 'default' libvirt network exists
                isolated_network=None,  # Optional second network
                os_variant="nixos-unstable",  # OS Hint for virt-install
                user_data=default_user_data,
                network_config=default_network_config,
                virt_install_extra_args=None,
            )
            # deploy_vm raises exceptions on failure
            vm_config = deploy_vm(cfg, ssh_key)
            log.info(f"Automatic deployment successful for {vm_name} ({vm_config.ip})")
            return vm_config

        finally:
            # Best effort cleanup of remote temp directory
            if remote_tmp_dir:
                log.info(f"Cleaning up remote temporary directory: {remote_tmp_dir}")
                try:
                    run_remote_command(
                        host,
                        ["rm", "-rf", str(remote_tmp_dir)],
                        ssh_key=ssh_key,
                        check=False,  # Don't raise on cleanup failure
                    )

                except Exception as cleanup_e:
                    log.warning(
                        f"Failed to cleanup remote temporary directory {remote_tmp_dir}: {cleanup_e}",
                        exc_info=False,
                    )
