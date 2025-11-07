#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300

import logging
import shlex  # For safer command printing if needed later
import subprocess
import sys
from pathlib import Path

# from .create import
from .remote import RemoteCommandError, run_remote_command

# Assume libvirt is available
try:
    import libvirt
except ImportError:
    print(
        "Error: libvirt-python bindings not found. Please install them.",
        file=sys.stderr,
    )
    sys.exit(1)

from .upload import upload  # Assuming upload raises exceptions on failure

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


def install_domain_with_virt_install(
    conn: libvirt.virConnect,
    name: str,
    memory_mb: int,
    vcpu: int,
    disk_volume_name: str,  # Name of the primary disk volume
    cdrom_volume_name: str | None,  # Name of the CDROM volume (ISO), None if not using CDROM
    pool_name: str,
    primary_network: str,
    isolated_network: str | None,
    os_variant: str,
    user_data_path: Path,
    network_config_path: Path,
    remote_user_host: str,
    remote_tmp_dir: Path,
    ssh_key: Path | None,
    libvirt_system_uri: str = "qemu:///system",
    extra_virt_install_args: list[str] | None = None,
) -> None:
    """
    Creates a domain using the virt-install command via SSH with cloud-init.

    Raises:
        RuntimeError: If virt-install fails, times out, or cloud-init files cannot be uploaded.
        RemoteCommandError: If SSH command execution fails.
        TimeoutError: If virt-install command times out.
        FileNotFoundError: If 'ssh' command is not found locally.
        libvirt.libvirtError: If checking domain existence fails (unexpectedly).
    """
    # 1. Check if domain already exists (best effort)
    try:
        domain = conn.lookupByName(name)
        state, _ = domain.state()
        state_str = (
            "running"
            if state == libvirt.VIR_DOMAIN_RUNNING
            else "defined but not running"
        )
        log.info(
            f"Domain '{name}' already exists ({state_str}). Skipping virt-install."
        )
        return  # Domain exists, nothing more to do
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
            log.info(f"Domain '{name}' not found. Proceeding with virt-install...")
        else:
            # Log other libvirt errors during lookup but proceed with install attempt
            log.warning(
                f"Error looking up domain '{name}' (will attempt install anyway): {e}"
            )
            # Do not raise here, allow install attempt

    # 2. Upload cloud-init files only if not using CDROM
    # (ISO installations don't use cloud-init)
    remote_user_data_path = remote_tmp_dir / f"{name}-user-data.cfg"
    remote_network_config_path = remote_tmp_dir / f"{name}-network-config.cfg"

    if not cdrom_volume_name:
        # 3. Upload cloud-init files (assuming vm_spawner.upload raises on error)
        try:
            log.info(
                f"Uploading {user_data_path} to {remote_user_host}:{remote_user_data_path}..."
            )
            upload(remote_user_host, user_data_path, remote_user_data_path, ssh_key=ssh_key)
            log.info(
                f"Uploading {network_config_path} to {remote_user_host}:{remote_network_config_path}..."
            )
            upload(
                remote_user_host,
                network_config_path,
                remote_network_config_path,
                ssh_key=ssh_key,
            )
        except Exception as upload_e:  # Catch specific upload errors if possible
            log.error(f"Failed to upload cloud-init files: {upload_e}", exc_info=True)
            msg = "Failed to upload cloud-init files"
            raise RuntimeError(msg) from upload_e
    else:
        log.info("Skipping cloud-init upload for ISO-based installation")

    shell_cmd = []
    # Check if virt-install should be run inside nix-shell
    use_nix_shell = True  # Or make this configurable if needed
    if use_nix_shell:
        shell_cmd.extend(["nix", "shell", "nixpkgs#virt-manager", "--command"])

    virt_install_cmd = [
        "virt-install",
        f"--connect={libvirt_system_uri}",
        f"--name={name}",
        f"--memory={memory_mb}",
        f"--vcpus={vcpu}"
    ]

    # Configure disk and boot based on whether we're using CDROM (ISO) or not
    if cdrom_volume_name:
        # ISO installation: add blank disk, CDROM, and virtfs filesystems for /nix/store and /nix/var/nix/db
        virt_install_cmd.extend([
            f"--disk=vol={pool_name}/{disk_volume_name},device=disk,bus=virtio",
            f"--disk=vol={pool_name}/{cdrom_volume_name},device=cdrom,bus=sata,readonly=on",
            "--filesystem=type=mount,source=/nix/store,target=hoststore,readonly=on",
            "--filesystem=type=mount,source=/nix/var/nix/db,target=hostdb,readonly=on",
        ])
    else:
        # Direct disk import: use the disk volume
        virt_install_cmd.append(
            f"--disk=vol={pool_name}/{disk_volume_name},device=disk,bus=virtio"
        )

    virt_install_cmd.append(f"--network=network={primary_network},model=virtio")
    if isolated_network:
        virt_install_cmd.append(f"--network=network={isolated_network},model=virtio")

    virt_install_cmd.extend([
        f"--os-variant={os_variant}",
    ])

    # Add import flag and cloud-init only if not using CDROM
    if not cdrom_volume_name:
        virt_install_cmd.append("--import")
        virt_install_cmd.append(
            f"--cloud-init=user-data={remote_user_data_path!s},network-config={remote_network_config_path!s}"
        )

    virt_install_cmd.extend([
        #"--graphics=none",
        "--console=pty,target_type=serial",
        "--machine=q35",
    ])

    # Boot configuration
    if cdrom_volume_name:
        # Boot from HD first, CDROM second - this way after installation completes,
        # the VM will boot from the installed OS instead of the installer
        # Using explicit boot device syntax for newer virt-install versions
        virt_install_cmd.append("--boot=boot0.dev=hd,boot1.dev=cdrom")
    else:
        virt_install_cmd.append("--boot=hd")

    virt_install_cmd.extend([
        "--noautoconsole",
        "--check=disk_size=off",
        "--video=qxl",
        "--rng=/dev/urandom,model=virtio",
        # "--debug", # Uncomment for verbose virt-install output
    ])
    if extra_virt_install_args:
        virt_install_cmd.extend(extra_virt_install_args)

    full_cmd = shell_cmd + virt_install_cmd
    log.info(
        "Executing virt-install command via SSH:\n%s",
        " ".join(map(shlex.quote, full_cmd)),
    )

    # 5. Execute virt-install via SSH and clean up remote files
    timeout_seconds = 600  # 10 minutes
    try:
        # Use run_remote_command which wraps subprocess and raises specific errors
        # Note: run_remote_command needs the command parts *after* the host argument
        run_remote_command(
            host=remote_user_host,  # This is slightly redundant with ssh_prefix, refactor if needed
            command=full_cmd,  # Pass only the command part
            timeout=timeout_seconds,
            ssh_key=ssh_key,
        )
        # run_remote_command already logs stdout/stderr, just log success here
        log.info(f"Domain '{name}' installed successfully via virt-install.")

    except RemoteCommandError as e:
        # This catches non-zero exit code from virt-install
        # Log the error details
        log.error(str(e))
        msg = f"virt-install failed for domain '{name}'"
        raise RuntimeError(msg)
    except TimeoutError:  # Assuming run_remote_command raises TimeoutError now
        log.error(
            f"virt-install command timed out after {timeout_seconds} seconds.",
            exc_info=False,
        )
        raise  # Re-raise the specific TimeoutError
    except FileNotFoundError:
        log.error("Local 'ssh' command not found.")
        raise  # Re-raise original FileNotFoundError
    except Exception as e:
        msg = f"Unexpected error during virt-install for {name}: {e}"
        log.error(msg)
        raise RuntimeError(msg)
    finally:
        # Best effort cleanup of remote cloud-init files (only if they were uploaded)
        if not cdrom_volume_name:
            log.info("Attempting cleanup of remote cloud-init files...")
            cleanup_cmd_parts = [
                "rm",
                "-f",
                str(remote_user_data_path),
                str(remote_network_config_path),
            ]
            try:
                # Run cleanup command directly, ignore failures (check=False)
                ssh_cleanup_cmd = [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    remote_user_host,
                    "--",
                    *cleanup_cmd_parts,
                ]
                subprocess.run(
                    ssh_cleanup_cmd, capture_output=True, text=True, check=False, timeout=30
                )
                log.info("Remote cloud-init file cleanup command executed.")
            except Exception as cleanup_e:
                # Log cleanup errors but don't let them mask the primary exception (if any)
                log.warning(
                    f"Failed to cleanup remote cloud-init files: {cleanup_e}",
                    exc_info=False,
                )
