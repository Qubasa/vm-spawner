#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300
import argparse
import json
import logging
import os
import sys
import traceback
from pathlib import Path

import libvirt

from .deploy_vm import deploy_vm_auto
from .destroy import delete_vm
from .remote import RemoteCommandError

# Configure logging to use stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr
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

            # Output JSON to stdout
            output = {
                "name": vm_info.name,
                "ip": vm_info.ip,
            }

            print(json.dumps(output, indent=2))
        elif args.subcommand == "destroy" or args.subcommand == "d":
            delete_vm(host=args.remote_user_host, domain_name=args.name, ssh_key=args.ssh_key)

            # Output JSON to stdout
            output = {
                "name": args.name,
            }
            print(json.dumps(output, indent=2))
        else:
            # Should be caught by argparse 'required=True' on subcommand
            log.error(
                f"Invalid subcommand '{args.subcommand}'. Use 'create' or 'destroy'."
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
        log.error(f"Operation failed: {e}")
        log.debug("Full traceback:", exc_info=True)
        exit_code = 1
    except KeyboardInterrupt:
        log.info("Operation cancelled by user.")
        exit_code = 1
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        log.debug("Full traceback:", exc_info=True)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
