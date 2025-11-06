#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300
import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

import libvirt

from .deploy_vm import deploy_vm_auto
from .destroy import delete_vm
from .remote import RemoteCommandError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)
log = logging.getLogger(__name__)


# --- Argument Parsing ---
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy or Destroy a KVM VM using cloud-init.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--remote-user-host",
        type=str,
        required=True,
        help="Remote host address in user@hostname format for SSH and libvirt.",
        metavar="USER@HOST",
    )

    subparsers = parser.add_subparsers(
        dest="subcommand", required=True, help="Sub-command help"
    )

    # --- Create Subcommand ---
    crreate_parser = subparsers.add_parser(
        "create", help="Create a new VM", aliases=["c"]
    )
    crreate_parser.add_argument(
        "--ssh-key", type=Path, help="SSH key for remote access.", metavar="SSH_KEY"
    )

    # --- Destroy Subcommand ---
    destroy_parser = subparsers.add_parser(
        "destroy", help="Destroy an existing VM", aliases=["d"]
    )
    destroy_parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Name of the VM to destroy.",
        metavar="VM_NAME",
    )
    destroy_parser.add_argument(
        "--ssh-key", type=Path, help="SSH key for remote access.", metavar="SSH_KEY"
    )


    args = parser.parse_args()
    return args


# --- Main Execution Logic ---
def main() -> None:
    args = parse_arguments()
    exit_code = 0
    try:
        if args.subcommand == "create" or args.subcommand == "c":
            vm_info = deploy_vm_auto(host=args.remote_user_host, ssh_key=args.ssh_key)

            # Check if using ISO installation
            base_image = os.environ.get("CLAN_BASE_IMAGE", "")
            is_iso = base_image.lower().endswith(".iso")

            print("\n--- Success ---")
            print(f"VM Deployed: {vm_info.name}")
            print(f"IP Address:  {vm_info.ip}")
            print(f"Host:        {args.remote_user_host}")

            if is_iso:
                print("\nISO installation started. The VM is now booting from the installer.")
                print("Connect to the VM console to complete installation:")
                print(f"ssh {args.remote_user_host} virsh console {vm_info.name}")
            else:
                print("\nConnect via SSH (once cloud-init completes):")
                print(f"ssh -J {args.remote_user_host} root@{vm_info.ip}")
                print("Password is: root:terraform")
        elif args.subcommand == "destroy" or args.subcommand == "d":
            delete_vm(host=args.remote_user_host, domain_name=args.name, ssh_key=args.ssh_key)
            print("\n--- Success ---")
            print(
                f"VM '{args.name}' deletion process completed on {args.remote_user_host}."
            )
        else:
            # Should be caught by argparse 'required=True' on subcommand
            print(
                f"Error: Invalid subcommand '{args.subcommand}'. Use 'create' or 'destroy'.",
                file=sys.stderr,
            )
            exit_code = 1

    except (
        RemoteCommandError,
        RuntimeError,
        libvirt.libvirtError,
        TimeoutError,
        FileNotFoundError,
        ValueError,
    ) as e:
        print("\n--- Error ---", file=sys.stderr)
        print(f"Operation failed: {e}", file=sys.stderr)
        # Add traceback for debugging if needed, or rely on logs
        # traceback.print_exc(file=sys.stderr)
        log.debug(
            "Full traceback:", exc_info=True
        )  # Log full trace if debug level is enabled
        exit_code = 1
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        exit_code = 1
    except Exception as e:
        print("\n--- Unexpected Error ---", file=sys.stderr)
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
