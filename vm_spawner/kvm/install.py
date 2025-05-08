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
    base_volume_name: str,  # Just the name of the volume (e.g., clone name)
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

    # 2. Define remote paths for cloud-init files
    remote_user_data_path = remote_tmp_dir / f"{name}-user-data.cfg"
    remote_network_config_path = remote_tmp_dir / f"{name}-network-config.cfg"

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
        f"--vcpus={vcpu}",
        f"--disk=vol={pool_name}/{base_volume_name},device=disk,bus=virtio",  # Reference the volume
        f"--network=network={primary_network},model=virtio",
    ]
    if isolated_network:
        virt_install_cmd.append(f"--network=network={isolated_network},model=virtio")

    virt_install_cmd.extend(
        [
            f"--os-variant={os_variant}",
            "--import",
            f"--cloud-init=user-data={remote_user_data_path!s},network-config={remote_network_config_path!s}",
            "--graphics=none",
            "--console=pty,target_type=serial",
            "--machine=q35",
            "--boot=hd",
            "--noautoconsole",
            "--check=disk_size=off",
            "--video=qxl",
            "--rng=/dev/urandom,model=virtio",
            # "--debug", # Uncomment for verbose virt-install output
        ]
    )
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
        log.error(
            f"virt-install for domain '{name}' failed.", exc_info=False
        )  # Log details from exception str
        print(str(e), file=sys.stderr)  # Print details for user
        msg = f"virt-install failed for domain '{name}'"
        raise RuntimeError(msg) from e
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
        log.exception(
            f"An unexpected error occurred during virt-install execution for {name}"
        )
        msg = f"Unexpected error during virt-install for {name}"
        raise RuntimeError(msg) from e
    finally:
        # Best effort cleanup of remote cloud-init files
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
