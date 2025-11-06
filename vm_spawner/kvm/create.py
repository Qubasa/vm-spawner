#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300

import contextlib
import logging
import shlex  # For safer command printing if needed later
import subprocess
import sys
import time
from pathlib import Path

from .download import download_file
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


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


def get_group_id(host: str, ssh_key: Path | None) -> str:
    """Gets the group ID of the default group ('kvm') on the remote host."""
    log.info(f"Getting gid for default group on {host}...")
    # run_remote_command will raise RemoteCommandError if 'id -g' fails
    group_id = run_remote_command(host, ["id", "-g"], ssh_key=ssh_key)
    log.info(f"Found group ID: {group_id}")
    return group_id.stdout


def get_or_create_pool(
    conn: libvirt.virConnect,
    host: str,
    name: str,
    pool_type: str,
    target_path: Path,
    ssh_key: Path | None,
) -> libvirt.virStoragePool:
    """
    Gets an existing storage pool or creates a new one using libvirt-python.

    Returns:
        The active libvirt.virStoragePool object.
    Raises:
        RuntimeError: If the pool cannot be found, created, built, or activated.
        libvirt.libvirtError: For underlying libvirt API errors.
        RemoteCommandError: If getting the remote group ID fails.
    """
    pool: libvirt.virStoragePool | None = None
    try:
        log.info(f"Looking up storage pool '{name}'...")
        pool = conn.storagePoolLookupByName(name)
        log.info(f"Found existing storage pool '{name}'.")
        if not pool.isActive():
            log.info(f"Activating pool '{name}'...")
            pool.create(0)
            log.info(f"Pool '{name}' activated.")
        return pool
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_STORAGE_POOL:
            log.info(f"Storage pool '{name}' not found. Creating...")
            # Get group ID *before* attempting creation
            group_id = get_group_id(host, ssh_key)

            pool_xml_desc = f"""
            <pool type='{pool_type}'>
              <name>{name}</name>
              <target>
                <path>{target_path!s}</path>
                <permissions>
                  <mode>0770</mode>
                  <owner>0</owner>
                  <group>{group_id}</group>
                  <label>virt_image_t</label>
                </permissions>
              </target>
            </pool>
            """
            log.info(f"Defining pool '{name}' with XML:\n{pool_xml_desc}")
            defined_pool: libvirt.virStoragePool | None = None
            try:
                defined_pool = conn.storagePoolDefineXML(pool_xml_desc, 0)
                if defined_pool is None:
                    # Should not happen if defineXML doesn't raise, but check anyway
                    msg = f"Failed to define pool '{name}' (defineXML returned None)."
                    log.error(msg)
                    raise RuntimeError(msg)

                log.info(f"Building pool '{name}'...")
                try:
                    # Build might try to create the directory
                    defined_pool.build(0)
                    log.info(f"Pool '{name}' built successfully.")
                except libvirt.libvirtError as build_e:
                    # For 'dir' type, build failure might be ok if dir exists
                    # Log as warning but proceed to activate
                    log.warning(
                        f"Failed to explicitly build pool '{name}' "
                        f"(may be harmless for '{pool_type}' type if path exists): {build_e}",
                        exc_info=False,
                    )

                log.info(f"Setting autostart for pool '{name}'...")
                defined_pool.setAutostart(1)
                log.info(f"Activating pool '{name}'...")
                defined_pool.create(0)  # Activate
                log.info(
                    f"Storage pool '{name}' created and activated at '{target_path}'."
                )
                return defined_pool  # Return the newly created and active pool

            except libvirt.libvirtError as create_e:
                log.error(
                    f"Failed to define, build, or activate pool '{name}': {create_e}",
                    exc_info=True,
                )
                # Attempt cleanup on failure
                with contextlib.suppress(libvirt.libvirtError):
                    if defined_pool:
                        log.info(f"Attempting to undefine failed pool '{name}'...")
                        defined_pool.undefine()
                msg = f"Failed to create pool '{name}'"
                raise RuntimeError(msg) from create_e
        else:
            # Error during initial lookup that wasn't NO_STORAGE_POOL
            log.error(f"Error looking up pool '{name}': {e}", exc_info=True)
            msg = f"Failed to look up storage pool '{name}'"
            raise RuntimeError(msg) from e


def ensure_volume_from_file(
    conn: libvirt.virConnect,
    pool: libvirt.virStoragePool,
    host: str,
    vol_name: str,
    source_file: Path,  # Local path to the source image file
    fmt: str,
    base_image_url: str,  # URL to download if source_file doesn't exist/is invalid
    base_image_checksum: str | None,
    ssh_key: Path | None,
) -> libvirt.virStorageVol:
    """
    Ensures a volume exists in the pool, creating/uploading it from a local file if needed.
    Downloads the file if it doesn't exist locally.

    Returns:
        The libvirt.virStorageVol object for the volume.
    Raises:
        RuntimeError: If the volume cannot be found, created, or uploaded, or if download fails.
        libvirt.libvirtError: For underlying libvirt API errors.
        FileNotFoundError: If the source file cannot be found after download attempt.
        ValueError: If checksum verification fails.
        RemoteCommandError: If getting remote group ID fails.
    """
    pool_name = pool.name()
    vol: libvirt.virStorageVol | None = None
    try:
        log.info(f"Looking up volume '{vol_name}' in pool '{pool_name}'...")
        vol = pool.storageVolLookupByName(vol_name)
        log.info(
            f"Found existing volume '{vol_name}' in pool '{pool_name}'. Path: {vol.path()}"
        )
        # Optional: Add check here if existing volume needs verification (e.g., size, checksum)
        return vol
    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_VOL:
            log.error(f"Error looking up volume '{vol_name}': {e}", exc_info=True)
            msg = f"Failed to look up volume '{vol_name}'"
            raise RuntimeError(msg) from e
        # Volume doesn't exist, proceed to create/upload
        log.info(f"Volume '{vol_name}' not found. Will create/upload.")

    # 1. Ensure Base Image is downloaded locally first
    log.info(f"Ensuring base image exists locally at {source_file}...")
    # download_file will raise exceptions on failure (network, disk space, checksum)
    download_file(base_image_url, source_file, base_image_checksum)
    log.info(f"Base image is available at {source_file}.")

    # 2. Check if source file exists *after* download attempt
    if not source_file.is_file():
        msg = f"Source file {source_file} not found or is not a file, even after download attempt."
        log.error(msg)
        raise FileNotFoundError(msg)

    # 3. Create and Upload
    log.info(
        f"Creating volume '{vol_name}' in pool '{pool_name}' from {source_file}..."
    )
    created_vol: libvirt.virStorageVol | None = None
    stream: libvirt.virStream | None = None
    try:
        file_size_bytes = source_file.stat().st_size
        log.info(f"Source file size: {file_size_bytes} bytes")
        group_id = get_group_id(host, ssh_key)  # Get group ID for permissions

        # Define volume metadata
        vol_xml_desc = f"""
        <volume type='file'>
          <name>{vol_name}</name>
          <capacity unit='bytes'>{file_size_bytes}</capacity>
          <target>
            <format type='{fmt}'/>
             <permissions>
               <mode>0644</mode>
               <owner>0</owner>
               <group>{group_id}</group>
             </permissions>
          </target>
        </volume>
        """
        log.info(f"Defining volume '{vol_name}' with XML:\n{vol_xml_desc}")
        created_vol = pool.createXML(vol_xml_desc, 0)
        if created_vol is None:
            msg = f"Failed to define volume '{vol_name}' in pool '{pool_name}' (createXML returned None)."
            log.error(msg)
            raise RuntimeError(msg)

        log.info(
            f"Volume '{vol_name}' defined (Path: {created_vol.path()}). Uploading content..."
        )

        # Upload the content via stream
        stream = conn.newStream(0)  # flags=0 for non-sparse
        created_vol.upload(stream, 0, file_size_bytes, 0)

        uploaded_bytes = 0
        start_time = time.time()
        with source_file.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)  # 1MB chunk
                if not chunk:
                    break
                # stream.sendall is preferable if available and works for your libvirt version
                try:
                    ret = stream.send(chunk)
                    if ret == -1:
                        # Error detail retrieval might vary or not work depending on stream state
                        # stream.finish() might give error details but might also block or fail
                        # Abort is generally safer if send failed midway
                        log.error(
                            f"Error sending data to stream for volume '{vol_name}'. Aborting."
                        )
                        stream.abort()
                        msg = f"Stream send error for volume '{vol_name}'"
                        raise RuntimeError(msg)
                except libvirt.libvirtError as send_e:
                    log.error(
                        f"Libvirt error during stream send for volume '{vol_name}': {send_e}",
                        exc_info=True,
                    )
                    stream.abort()  # Ensure abort on libvirt error too
                    msg = f"Stream send libvirt error for volume '{vol_name}'"
                    raise RuntimeError(msg) from send_e

                uploaded_bytes += len(chunk)
                # Progress reporting (optional)
                elapsed_time = time.time() - start_time
                speed = (
                    (uploaded_bytes / elapsed_time / 1024 / 1024)
                    if elapsed_time > 0
                    else 0.0
                )
                percent = (
                    (uploaded_bytes / file_size_bytes) * 100
                    if file_size_bytes > 0
                    else 0.0
                )
                # Progress logging (debug level to avoid clutter)
                log.debug(
                    f"Uploaded {uploaded_bytes / 1024 / 1024:.2f} / {file_size_bytes / 1024 / 1024:.2f} MB"
                    f" ({percent:.1f}%) at {speed:.2f} MB/s"
                )

        log.debug("Finishing upload stream...")
        ret = stream.finish()
        if ret == -1:
            # Finish failed after all data supposedly sent
            log.error(f"Error finishing stream for volume '{vol_name}'.")
            msg = f"Stream finish error for volume '{vol_name}'"
            raise RuntimeError(msg)

        log.info(f"Volume '{vol_name}' created and uploaded successfully.")
        return created_vol

    except (libvirt.libvirtError, OSError, RemoteCommandError) as e:
        log.error(f"Failed to create or upload volume '{vol_name}': {e}", exc_info=True)
        # Attempt cleanup on failure
        with contextlib.suppress(libvirt.libvirtError):
            if stream:
                stream.abort()  # Ensure stream is aborted
        with contextlib.suppress(libvirt.libvirtError):
            if created_vol:
                log.info(
                    f"Attempting to delete partially created/uploaded volume '{vol_name}'..."
                )
                created_vol.delete(0)
        # Re-raise as a generic runtime error wrapping the original cause
        msg = f"Failed to ensure volume '{vol_name}' exists"
        raise RuntimeError(msg) from e
    except Exception as e:
        # Catch unexpected errors
        log.exception(f"An unexpected error occurred ensuring volume '{vol_name}'")
        with contextlib.suppress(libvirt.libvirtError):
            if stream:
                stream.abort()
        with contextlib.suppress(libvirt.libvirtError):
            if created_vol:
                created_vol.delete(0)
        msg = f"Unexpected error ensuring volume '{vol_name}'"
        raise RuntimeError(msg) from e


def create_blank_disk(
    storage_pool: libvirt.virStoragePool,
    remote_host: str,
    disk_name: str,
    disk_size_gb: int,
    ssh_key: Path | None,
) -> Path:
    """
    Creates a blank qcow2 disk on the remote host using qemu-img.

    Args:
        storage_pool: The libvirt storage pool where the disk will reside.
        remote_host: The user@host string for SSH access.
        disk_name: The desired name for the disk volume (without extension).
        disk_size_gb: Size of the disk in GB.
        ssh_key: Path to the SSH key for remote access.

    Returns:
        The Path object representing the remote path of the created blank disk.
    Raises:
        RuntimeError: If the blank disk cannot be created or the pool refreshed.
        RemoteCommandError: If the ssh commands fail.
    """
    try:
        # Get pool path
        pool_xml = storage_pool.XMLDesc(0)
        import xml.etree.ElementTree as ET
        pool_root = ET.fromstring(pool_xml)
        pool_path_elem = pool_root.find(".//target/path")
        if pool_path_elem is None or pool_path_elem.text is None:
            msg = f"Could not determine path for pool '{storage_pool.name()}'"
            raise RuntimeError(msg)
        pool_path = Path(pool_path_elem.text)

        # Create disk filename
        disk_filename = f"{disk_name}.qcow2"
        remote_disk_path = pool_path / disk_filename

        log.info(f"Creating blank disk at: {remote_disk_path}")

        # Check if disk already exists
        check_disk_cmd = ["test", "-f", str(remote_disk_path)]
        log.info(
            f"Checking if remote blank disk exists: {' '.join(map(shlex.quote, check_disk_cmd))}"
        )
        try:
            check_result = run_remote_command(
                remote_host,
                check_disk_cmd,
                check=False,
                ssh_key=ssh_key,
                timeout=30,
            )
            if check_result.returncode == 0:
                log.info(
                    f"Remote blank disk {remote_disk_path} already exists. Skipping creation."
                )
                storage_pool.refresh(0)
                return remote_disk_path
            if check_result.returncode == 1:
                log.info("Remote blank disk does not exist. Creating...")
            else:
                msg = "'test -f' command failed unexpectedly"
                raise RemoteCommandError(
                    msg,
                    command=check_disk_cmd,
                    returncode=check_result.returncode,
                    stderr=check_result.stderr,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            msg = f"Failed to check for existing blank disk: {e}"
            raise RemoteCommandError(msg, command=check_disk_cmd) from e

        # Create the blank disk using qemu-img
        qemu_img_cmd = [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            str(remote_disk_path),
            f"{disk_size_gb}G",
        ]
        run_remote_command(
            remote_host, qemu_img_cmd, timeout=120, ssh_key=ssh_key
        )

        log.info(f"Blank disk created successfully at {remote_disk_path}.")
        log.info(
            f"Refreshing pool '{storage_pool.name()}' after creating blank disk..."
        )
        storage_pool.refresh(0)

        return remote_disk_path

    except libvirt.libvirtError as e:
        log.error(
            f"Libvirt error during blank disk creation or pool refresh: {e}",
            exc_info=True,
        )
        msg = "Libvirt operation failed during blank disk creation"
        raise RuntimeError(msg) from e
    except RemoteCommandError as e:
        log.error(
            f"Remote command failed during blank disk creation: {e}", exc_info=False
        )
        raise
    except Exception as e:
        log.exception("An unexpected error occurred creating blank disk.")
        msg = "Unexpected error creating blank disk"
        raise RuntimeError(msg) from e


def create_linked_clone_disk(
    storage_pool: libvirt.virStoragePool,
    remote_host: str,
    base_volume: libvirt.virStorageVol,  # Changed to Vol object
    clone_img_name: str,  # Name for the new clone image file (e.g., vm_name)
    ssh_key: Path | None,  # Path to the SSH key for remote access
) -> Path:
    """
    Creates a linked clone (qcow2 overlay) on the remote host using qemu-img.

    Args:
        storage_pool: The libvirt storage pool where the clone will reside.
        remote_host: The user@host string for SSH access.
        base_volume: The libvirt storage volume object of the base image.
        clone_img_name: The desired name for the clone volume (without extension).

    Returns:
        The Path object representing the remote path of the *created* clone disk.
    Raises:
        RuntimeError: If the clone disk cannot be created or the pool refreshed.
        RemoteCommandError: If the ssh commands fail.
        libvirt.libvirtError: If base volume path cannot be retrieved or pool refresh fails.
    """
    try:
        remote_src = Path(base_volume.path())

        # Use clone_img_name for the filename, ensure .qcow2 extension
        clone_disk_filename = f"{clone_img_name}.qcow2"
        remote_dst = remote_src.parent / clone_disk_filename

        log.info(f"Base volume path: {remote_src}")
        log.info(f"Target clone disk path: {remote_dst}")

        # Check if clone disk already exists remotely using 'test -f'
        check_disk_cmd = ["test", "-f", str(remote_dst)]
        log.info(
            f"Checking if remote clone disk exists: {' '.join(map(shlex.quote, check_disk_cmd))}"
        )
        try:
            check_result = run_remote_command(
                remote_host,
                check_disk_cmd,
                check=False,  # Don't raise on non-zero exit code
                ssh_key=ssh_key,
                timeout=30,
            )
            if check_result.returncode == 0:
                log.info(
                    f"Remote clone disk {remote_dst} already exists. Skipping creation."
                )
                # Refresh pool just in case libvirt doesn't know about it yet
                log.info(f"Refreshing pool '{storage_pool.name()}'...")
                storage_pool.refresh(0)
                return remote_dst  # Return path to existing clone
            if check_result.returncode == 1:
                log.info("Remote clone disk does not exist. Creating...")
                # Proceed to create
            else:
                # Unexpected return code from 'test'
                msg = "'test -f' command failed unexpectedly"
                raise RemoteCommandError(
                    msg,
                    command=check_disk_cmd,
                    returncode=check_result.returncode,
                    stderr=check_result.stderr,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            msg = f"Failed to check for existing clone disk: {e}"
            raise RemoteCommandError(msg, command=check_disk_cmd) from e

        # Create the linked clone using qemu-img via run_remote_command
        qemu_img_cmd = [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",  # Assume backing file format is qcow2
            "-b",
            str(remote_src),  # Backing file path
            str(remote_dst),  # New clone path
        ]
        # run_remote_command will raise RemoteCommandError on failure
        run_remote_command(
            remote_host, qemu_img_cmd, timeout=120, ssh_key=ssh_key
        )  # Increased timeout for image creation

        log.info(f"Linked clone disk created successfully at {remote_dst}.")
        log.info(
            f"Refreshing pool '{storage_pool.name()}' after creating clone disk..."
        )
        storage_pool.refresh(0)  # Refresh pool so libvirt sees the new file

        return remote_dst

    except libvirt.libvirtError as e:
        log.error(
            f"Libvirt error during clone disk creation or pool refresh: {e}",
            exc_info=True,
        )
        msg = "Libvirt operation failed during linked clone creation"
        raise RuntimeError(msg) from e
    except RemoteCommandError as e:
        log.error(
            f"Remote command failed during linked clone creation: {e}", exc_info=False
        )
        raise  # Re-raise the specific error
    except Exception as e:
        log.exception("An unexpected error occurred creating linked clone disk.")
        msg = "Unexpected error creating linked clone disk"
        raise RuntimeError(msg) from e
