#!/usr/bin/env python3

import argparse
import logging
import os
from pathlib import Path

from vm_spawner.custom_logger import setup_logging
from vm_spawner.data import ArgMachine, Config, Provider, SSHKeyPair
from vm_spawner.dirs import user_cache_dir, user_data_dir
from vm_spawner.errors import VmSpawnError
from vm_spawner.ssh import generate_ssh_key, ssh_into_machine
from vm_spawner.terraform import tr_create, tr_destroy, tr_metadata

log = logging.getLogger(__name__)


def parse_machine_arg(machine_str: str) -> ArgMachine:
    """Parses a machine string argument into an ArgMachine dictionary."""
    parts = machine_str.split("|")
    if len(parts) < 2 or len(parts) > 3:
        msg = f"Invalid machine format: '{machine_str}'. Expected '<name>|<arch>[|<os_image>]'."
        raise argparse.ArgumentTypeError(msg)

    name = parts[0].strip()
    arch = parts[1].strip()
    os_image = parts[2].strip() if len(parts) == 3 else None

    if not name:
        msg = f"Machine name cannot be empty in '{machine_str}'."
        raise argparse.ArgumentTypeError(msg)
    if not arch:
        msg = f"Machine architecture cannot be empty in '{machine_str}'."
        raise argparse.ArgumentTypeError(msg)

    return {"name": name, "arch": arch, "os_image": os_image}


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    subparsers = parser.add_subparsers(dest="subcommand")

    create_parser = subparsers.add_parser(
        "create", help="Create resources", aliases=["c"]
    )

    create_parser.add_argument(
        "-m",
        "--machine",
        action="append",
        type=parse_machine_arg,  # Use the custom parser function
        help="Specify a machine in the format '<name>|<arch>[|<os_image>]'. Can be used multiple times.",
        default=[],
    )
    create_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    create_parser.add_argument(
        "-p",
        "--provider",
        help="Cloud provider to use",
        choices=[p.value for p in Provider],
        default=Provider.Hetzner.value,
    )
    create_parser.add_argument(
        "--ssh-pubkey",
        help="SSH pubkey path",
        type=Path,
    )
    create_parser.add_argument("--location", help="Server location")

    destroy_parser = subparsers.add_parser(
        "destroy", help="Destroy resources", aliases=["d"]
    )
    destroy_parser.add_argument(
        "--debug", action="store_true", help="Enable debug mode"
    )
    destroy_parser.add_argument(
        "--provider",
        choices=[p.value for p in Provider],
        default=Provider.Hetzner.value,
    )
    destroy_parser.add_argument(
        "--force", action="store_true", help="Delete local data even if remote fails"
    )

    metadata_parser = subparsers.add_parser("meta", help="Show metadata", aliases=["m"])
    metadata_parser.add_argument(
        "--debug", action="store_true", help="Enable debug mode"
    )
    metadata_parser.add_argument(
        "--provider",
        help="Cloud provider to use",
        choices=[p.value for p in Provider],
        default=Provider.Hetzner.value,
    )

    ssh_parser = subparsers.add_parser("ssh", help="SSH into a machine", aliases=["s"])
    ssh_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    ssh_parser.add_argument("machine", help="Machine to SSH into")
    ssh_parser.add_argument(
        "--provider",
        help="Cloud provider to use",
        choices=[p.value for p in Provider],
        default=Provider.Hetzner.value,
    )

    return parser


def create_conf_obj(args: argparse.Namespace) -> Config:
    is_debug = getattr(args, "debug", False)
    data_dir = user_data_dir() / "vm_spawner"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = user_cache_dir() / "vm_spawner"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tr_dir = data_dir / "terraform"
    clan_dir = data_dir / "clan"

    gen_key = generate_ssh_key(data_dir)
    ssh_keys = [gen_key]

    pubkey_path: Path | None = None
    if getattr(args, "ssh_pubkey", False):
        pubkey_path = Path(args.ssh_pubkey)
        assert pubkey_path is not None
        ssh_keys.append(
            SSHKeyPair(private=pubkey_path.with_suffix(""), public=pubkey_path)
        )

    if pubkey_path_str := os.environ.get("SSH_PUBKEY_PATH"):
        pubkey_path = Path(pubkey_path_str)
        ssh_keys.append(
            SSHKeyPair(
                private=Path(pubkey_path).with_suffix(""), public=Path(pubkey_path)
            )
        )

    return Config(
        debug=is_debug,
        data_dir=data_dir,
        tr_dir=tr_dir,
        cache_dir=cache_dir,
        clan_dir=clan_dir,
        ssh_keys=ssh_keys,
    )


def run_cli() -> None:
    parser = create_parser()
    args = parser.parse_args()

    config = create_conf_obj(args)

    if config.debug:
        setup_logging(logging.DEBUG)
        setup_logging(logging.DEBUG, root_log_name=__name__.split(".")[0])
    else:
        setup_logging(logging.INFO)
        setup_logging(logging.INFO, root_log_name=__name__.split(".")[0])

    log.debug("Debug mode enabled")

    if getattr(args, "provider", False):
        provider = Provider.from_str(args.provider)

    if args.subcommand == "create" or args.subcommand == "c":
        machines: list[ArgMachine] = args.machine
        if len(machines) == 0:
            msg = "No machines specified for creation. Add -m <machine>"
            raise VmSpawnError(msg)

        tr_create(
            config,
            provider,
            args.location,
            machines=machines,
        )

    elif args.subcommand == "destroy" or args.subcommand == "d":
        tr_destroy(config, provider)

    elif args.subcommand == "meta" or args.subcommand == "m":
        meta = tr_metadata(config)
        for machine in meta:
            print(machine)

    elif args.subcommand == "ssh" or args.subcommand == "s":
        tmachines = tr_metadata(config)
        ssh_into_machine(tmachines, args.machine, config.ssh_keys[0])

    else:
        parser.print_help()
