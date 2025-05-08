#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300

import logging
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from .connect import connect_libvirt

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


def delete_vm(host: str, domain_name: str, ssh_key: Path | None = None) -> None:
    """
    Connects to libvirt, deletes the specified domain (VM), and attempts
    to delete its associated storage volumes (disks identified in XML).

    Args:
        host: The user@host string for libvirt connection.
        domain_name: The name of the domain (VM) to delete.

    Raises:
        libvirt.libvirtError: If connection fails or critical libvirt
                              operations encounter unexpected errors (e.g., cannot
                              undefine). Errors finding/deleting disks are logged
                              but may not cause a raise if undefine succeeds.
        RuntimeError: For other unexpected errors during the process.
    """
    if ssh_key:
        # Use SSH URI for remote connection
        libvirt_uri = f"qemu+ssh://{host}/system?keyfile={ssh_key}"
    else:
        libvirt_uri = f"qemu+ssh://{host}/system"
    conn: libvirt.virConnect | None = None
    dom: libvirt.virDomain | None = None
    disk_paths: list[str] = []
    pool_and_vol_names: list[tuple[str, str]] = []  # Store (pool_name, vol_name)

    try:
        log.info(f"Attempting deletion of VM: {domain_name} via {libvirt_uri}")
        conn = connect_libvirt(libvirt_uri)  # Raises on connection failure

        # --- 1. Find the Domain ---
        try:
            log.info(f"Looking up domain: {domain_name}")
            dom = conn.lookupByName(domain_name)
            log.info(f"Domain {domain_name} found.")
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                log.warning(
                    f"Domain {domain_name} not found. Assuming already deleted."
                )
                return  # Nothing more to do
            log.error(f"Error looking up domain {domain_name}: {e}", exc_info=True)
            raise  # Reraise unexpected lookup errors

        # --- 2. Ensure Domain is Not Running ---
        if dom.isActive():
            log.info(
                f"Domain {domain_name} is active. Attempting to destroy (force stop)..."
            )
            try:
                dom.destroy()
                time.sleep(2)  # Give time for resources to release
                log.info(f"Domain {domain_name} destroyed.")
            except libvirt.libvirtError as e:
                log.error(f"Failed to destroy domain {domain_name}: {e}", exc_info=True)
                # If destroy fails, undefine might also fail. Raise here.
                msg = f"Failed to destroy running domain {domain_name}"
                raise RuntimeError(msg) from e

        # --- 3. Get Disk Information BEFORE Undefining ---
        try:
            log.info(f"Retrieving XML description for {domain_name} to find disks...")
            xml_desc = dom.XMLDesc(0)
            root = ET.fromstring(xml_desc)
            devices = root.find("devices")
            if devices is not None:
                for disk in devices.findall("disk"):
                    if disk.get("device") == "disk":  # Look for actual disks
                        source = disk.find("source")
                        if source is not None:
                            # Prefer volume/pool info if available (more reliable for deletion)
                            pool_name = source.get("pool")
                            vol_name = source.get("volume")
                            file_path = source.get("file")  # Fallback: path

                            if pool_name and vol_name:
                                log.info(
                                    f"Found managed disk: pool='{pool_name}', volume='{vol_name}'"
                                )
                                pool_and_vol_names.append((pool_name, vol_name))
                            elif file_path:
                                log.info(
                                    f"Found disk by path (will attempt lookup): {file_path}"
                                )
                                disk_paths.append(file_path)
                            else:
                                log.warning(
                                    "Disk source found without pool/volume or file path."
                                )
        except Exception as e:
            log.error(
                f"Error parsing XML or finding disks for {domain_name}: {e}",
                exc_info=True,
            )
            # Continue to undefine, but disk deletion might be incomplete

        # --- 4. Undefine (Delete) the Domain Configuration ---
        try:
            log.info(f"Attempting to undefine domain {domain_name}...")
            flags = (
                libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
                | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
                | libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
            )
            dom.undefineFlags(flags)
            log.info(f"Domain {domain_name} undefined successfully.")
            dom = None  # Domain object is now invalid
        except libvirt.libvirtError as e:
            log.error(f"Failed to undefine domain {domain_name}: {e}", exc_info=True)
            # Reraise as undefine is critical for deletion
            msg = f"Failed to undefine domain {domain_name}"
            raise RuntimeError(msg) from e

        # --- 5. Delete Associated Storage Volumes ---
        volumes_to_delete: list[libvirt.virStorageVol] = []

        # Find volumes by pool/name
        for pool_name, vol_name in pool_and_vol_names:
            try:
                pool = conn.storagePoolLookupByName(pool_name)
                vol = pool.storageVolLookupByName(vol_name)
                log.info(
                    f"Found volume '{vol_name}' in pool '{pool_name}' for deletion."
                )
                volumes_to_delete.append(vol)
            except libvirt.libvirtError as e:
                log.warning(
                    f"Could not find volume '{vol_name}' in pool '{pool_name}' for deletion: {e}"
                )

        # Find volumes by path (fallback)
        for path in disk_paths:
            try:
                vol = conn.storageVolLookupByPath(path)
                # Avoid adding duplicates if found by path and pool/name
                if vol and vol not in volumes_to_delete:
                    log.info(
                        f"Found volume by path '{path}' (Name: {vol.name()}) for deletion."
                    )
                    volumes_to_delete.append(vol)
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_STORAGE_VOL:
                    log.warning(f"No storage volume found for path '{path}'.")
                else:
                    log.warning(
                        f"Error looking up storage volume by path '{path}': {e}"
                    )

        # Delete the collected volumes
        if not volumes_to_delete:
            log.info("No associated storage volumes found or identified for deletion.")
        else:
            log.info(
                f"Attempting to delete {len(volumes_to_delete)} associated storage volume(s)..."
            )
            for vol in volumes_to_delete:
                try:
                    vol_name = (
                        vol.name()
                    )  # Get name before potential deletion invalidates object
                    vol_path = vol.path()
                    log.info(
                        f"Deleting storage volume: {vol_name} (Path: {vol_path})..."
                    )
                    # Use flags=0 for standard delete. Add flags if needed (e.g., snapshots).
                    vol.delete(0)
                    log.info(f"Successfully deleted storage volume: {vol_name}")
                except libvirt.libvirtError as e_del:
                    # Log deletion errors but don't raise an exception here,
                    # as the domain is already undefined.
                    log.error(
                        f"Failed to delete storage volume {vol_name} ({vol_path}): {e_del}",
                        exc_info=False,
                    )

        log.info(f"VM '{domain_name}' deletion process completed.")

    except (libvirt.libvirtError, RuntimeError) as e:
        log.error(f"VM deletion failed for {domain_name}: {e}", exc_info=True)
        raise  # Re-raise critical errors
    except Exception as e:
        log.exception(
            f"An unexpected error occurred during VM deletion for {domain_name}"
        )
        msg = f"Unexpected error deleting VM {domain_name}"
        raise RuntimeError(msg) from e
    finally:
        # --- 6. Close Connection ---
        if dom is not None:
            # This shouldn't happen if undefine was successful
            log.warning(
                f"Domain object for '{domain_name}' still exists after deletion attempt."
            )
        if conn is not None:
            try:
                conn.close()
                log.info("Libvirt connection closed.")
            except libvirt.libvirtError as e:
                log.warning(f"Error closing libvirt connection: {e}", exc_info=False)
