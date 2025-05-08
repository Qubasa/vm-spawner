#!/usr/bin/env python3
import contextlib
import sys
import time
import xml.etree.ElementTree as ET

import libvirt


def get_domain_ip_from_network(
    conn: libvirt.virConnect,
    domain_name: str,
    network_name: str,
    retries: int = 60,
    delay: float = 1.0,
    verbose: bool = False,
) -> str | None:
    """
    Retrieves the DHCP-assigned IP address for a given domain connected to a specific network.

    Args:
        conn: An active libvirt connection object.
        domain_name: The name of the virtual machine (domain).
        network_name: The name of the libvirt network.
        retries: Number of times to check for the DHCP lease.
        delay: Seconds to wait between retries.
        verbose: If True, print verbose P R O C E S S I N G: messages during retries.

    Returns:
        The IPv4 address string if found, otherwise None.
        Returns None immediately if the domain or network doesn't exist or the domain is not running.
    """
    if verbose:
        print(
            f"Attempting to find IP for domain '{domain_name}' on network '{network_name}'..."
        )

    domain: libvirt.virDomain | None = None
    network: libvirt.virNetwork | None = None

    try:
        domain = conn.lookupByName(domain_name)
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
            print(f"Error: Domain '{domain_name}' not found.", file=sys.stderr)
            return None
        print(f"Error looking up domain '{domain_name}': {e}", file=sys.stderr)
        return None

    try:
        network = conn.networkLookupByName(network_name)
    except libvirt.libvirtError as e:
        if e.get_error_code() == libvirt.VIR_ERR_NO_NETWORK:
            print(f"Error: Network '{network_name}' not found.", file=sys.stderr)
            return None
        print(f"Error looking up network '{network_name}': {e}", file=sys.stderr)
        return None

    if not domain.isActive():
        print(f"Info: Domain '{domain_name}' is not running.", file=sys.stderr)
        return None

    if not network.isActive():
        print(f"Info: Network '{network_name}' is not active.", file=sys.stderr)
        return None

    target_macs = []
    try:
        # Parse the domain's XML to find interfaces connected to the target network
        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        for device in root.findall("./devices/interface"):
            source = device.find("source")
            mac = device.find("mac")
            if source is not None and mac is not None:
                source_network = source.get("network")
                mac_address = mac.get("address")
                if source_network == network_name and mac_address:
                    target_macs.append(mac_address.lower())  # Store MACs in lower case

        if not target_macs:
            print(
                f"Warning: No interface found for domain '{domain_name}' connected to network '{network_name}'.",
                file=sys.stderr,
            )
            return None
        if verbose:
            print(
                f"Found MAC addresses for '{domain_name}' on '{network_name}': {target_macs}"
            )

    except ET.ParseError as e:
        print(f"Error parsing XML for domain '{domain_name}': {e}", file=sys.stderr)
        return None
    except Exception as e:  # Catch potential other errors during XML processing
        print(
            f"Unexpected error processing domain XML for '{domain_name}': {e}",
            file=sys.stderr,
        )
        return None

    for attempt in range(retries):
        if verbose:
            print(
                f"Attempt {attempt + 1}/{retries}: Querying DHCP leases on '{network_name}'..."
            )
        try:
            # Get DHCP leases for the network
            # Note: Returns leases for *all* VMs on the network
            leases = network.DHCPLeases()  # Timeout parameter is optional
            if not leases:
                if verbose:
                    print("No active DHCP leases found on the network yet.")

            for lease in leases:
                # Example lease format:
                # {'expirytime': 1678886400, 'mac': '52:54:00:xx:yy:zz', 'ipaddr': '192.168.122.100',
                #  'prefix': 24, 'hostname': 'vm-name', 'clientid': '...', 'iaid': '...'}
                # 'type': libvirt.VIR_IP_ADDR_TYPE_IPV4 (or IPV6)

                lease_mac = lease.get("mac", "").lower()
                ip_addr = lease.get("ipaddr")
                ip_type = lease.get("type")  # Check if it's an IPv4 address

                if (
                    ip_addr
                    and lease_mac in target_macs
                    and ip_type == libvirt.VIR_IP_ADDR_TYPE_IPV4
                ):
                    if verbose:
                        print(f"Found matching lease: MAC={lease_mac}, IP={ip_addr}")
                    print(
                        f"Success: IP address for '{domain_name}' on network '{network_name}' is {ip_addr}"
                    )
                    return ip_addr  # Found the IP for our VM's MAC

        except libvirt.libvirtError as e:
            # Handle cases where DHCP might not be enabled or network is down
            print(
                f"Warning: libvirt error getting DHCP leases (attempt {attempt + 1}): {e}",
                file=sys.stderr,
            )
            # Decide if this error is fatal or worth retrying
            if (
                "network is not active" in str(e).lower()
                or "DHCP server is not running" in str(e).lower()
            ):
                print(
                    "Error: Cannot get leases from inactive network or network without DHCP.",
                    file=sys.stderr,
                )
                return (
                    None  # Don't retry if the network fundamentally won't give leases
                )

        except Exception as e:
            print(
                f"Unexpected error getting DHCP leases (attempt {attempt + 1}): {e}",
                file=sys.stderr,
            )
            # Could be temporary, so continue retrying unless it's clearly fatal

        if attempt < retries - 1:
            if verbose:
                print(f"IP not found yet, waiting {delay} seconds...")
            time.sleep(delay)

    print(
        f"Error: Could not find DHCP lease for domain '{domain_name}' on network '{network_name}' after {retries} attempts.",
        file=sys.stderr,
    )
    return None


# --- Example Usage ---
if __name__ == "__main__":
    # --- Configuration ---
    LIBVIRT_URI = "qemu:///system"  # Or "qemu+ssh://user@host/system" for remote
    TARGET_DOMAIN = "ubuntu-py-vm"  # Change to the actual name of your VM
    TARGET_NETWORK = "default"  # Change to the network name VM is connected to
    # --- End Configuration ---

    print(f"Connecting to libvirt at {LIBVIRT_URI}...")
    conn: libvirt.virConnect | None = None
    try:
        conn = libvirt.open(LIBVIRT_URI)
        if conn is None:
            print(f"Failed to open connection to {LIBVIRT_URI}", file=sys.stderr)
            sys.exit(1)

        print(
            f"Connected. Trying to get IP for '{TARGET_DOMAIN}' on network '{TARGET_NETWORK}'..."
        )

        ip_address = get_domain_ip_from_network(
            conn,
            TARGET_DOMAIN,
            TARGET_NETWORK,
            retries=15,  # Increase retries if VM boot is slow
            delay=4,  # Increase delay if needed
            verbose=True,  # Show detailed steps
        )

        if ip_address:
            print(f"\n*** Found IP Address: {ip_address} ***")
        else:
            print(
                f"\n*** Failed to retrieve IP address for {TARGET_DOMAIN} on {TARGET_NETWORK}. ***"
            )
            print("Possible reasons:")
            print("- VM is not running or hasn't booted far enough to get DHCP.")
            print("- VM is connected to a different network.")
            print(f"- Network '{TARGET_NETWORK}' does not have DHCP enabled.")
            print("- VM has a static IP configuration.")
            print("- Network or Domain name is incorrect.")

    except libvirt.libvirtError as e:
        print(f"Libvirt error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if conn:
            with contextlib.suppress(libvirt.libvirtError):
                conn.close()
                print("Libvirt connection closed.")
