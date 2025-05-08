#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300

import logging

import libvirt

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


def connect_libvirt(uri: str) -> libvirt.virConnect:
    """Connects to the libvirt daemon."""
    log.info(f"Connecting to libvirt at {uri}...")
    try:
        # Use context manager for potential future resource management
        conn = libvirt.open(uri)
        if conn is None:
            # This case is unlikely based on libvirt docs, error usually raises
            msg = f"Failed to open connection to {uri} (returned None)."
            log.error(msg)
            raise libvirt.libvirtError(msg)  # Use libvirtError for consistency
        log.info(
            f"Connected to: {conn.getHostname()} (Libvirt version: {conn.getLibVersion()})"
        )
        return conn
    except libvirt.libvirtError as e:
        log.error(f"Failed to connect to libvirt at {uri}: {e}", exc_info=True)
        raise  # Re-raise the specific libvirt error
