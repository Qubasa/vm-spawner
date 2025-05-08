#!/usr/bin/env python3

import enum
import json
import logging
import os
import shutil
import subprocess
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from vm_spawner import hetzner
from vm_spawner.assets import get_cloud_asset
from vm_spawner.data import ArgMachine, Config, Provider, TrMachine
from vm_spawner.errors import VmSpawnError

log = logging.getLogger(__name__)


class PromptType(enum.Enum):
    LINE = "line"
    HIDDEN = "hidden"
    MULTILINE = "multiline"


def ask(
    ident: str,
    input_type: PromptType,
    label: str | None,
) -> str:
    text = f"Enter the value for {ident}:"
    if label:
        text = f"{label}"
    log.info(f"Prompting value for {ident}")
    match input_type:
        case PromptType.LINE:
            result = input(f"{text}: ")
        case PromptType.MULTILINE:
            print(f"{text} (Finish with Ctrl-D): ")
            result = sys.stdin.read()
        case PromptType.HIDDEN:
            result = getpass(f"{text} (hidden): ")

    log.info("Input received. Processing...")
    return result


def copy_from_nixstore(src: Path, dest: Path) -> None:
    subprocess.run(["cp", "-r", str(src), str(dest)])
    subprocess.run(["chmod", "-R", "u+w", str(dest)])


def tr_init(config: Config, provider: Provider) -> None:
    log.debug(f"Data dir: {config.data_dir}")
    tr_folder = get_cloud_asset(provider, "terraform")
    tr_dest_folder = config.tr_dir
    providers_cache_dir = config.cache_dir / ".terraform"
    providers_cache_dir.mkdir(parents=True, exist_ok=True)
    providers_dir = tr_dest_folder / ".terraform"

    if not tr_dest_folder.exists():
        copy_from_nixstore(tr_folder, tr_dest_folder)

        log.info(f"Symlink: {providers_cache_dir} -> {providers_dir}")
        providers_dir.symlink_to(providers_cache_dir)

        subprocess.run(
            ["tofu", f"-chdir={config.tr_dir}", "init"],
            check=True,
        )


def tr_metadata(config: Config) -> list[TrMachine]:
    res = subprocess.run(
        ["tofu", f"-chdir={config.tr_dir}", "output", "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    jdata = json.loads(res.stdout)

    machines = []
    for _name, data in jdata["vm_info"]["value"].items():
        tr_machine = TrMachine(
            name=data["name"],
            location=data.get("location"),
            server_type=data["server_type"],
            os_image=data["os_image"],
            arch=data["arch"],
            ipv4=data["ipv4"],
            ipv6=data.get("ipv6"),
            internal_ipv6=data.get("internal_ipv6"),
            provider=Provider.from_str(data["provider"]),
        )
        machines.append(tr_machine)

    return machines


def tr_clean(config: Config) -> None:
    tr_folder = config.tr_dir
    shutil.rmtree(tr_folder, ignore_errors=True)


def tr_write_vars(config: Config, data: dict[str, Any]) -> None:
    vars_file = config.tr_dir / "servers.auto.tfvars.json"
    with vars_file.open("w") as json_file:
        json.dump(data, json_file, indent=2)


def tr_ask_for_api_key(provider: Provider) -> None:
    match provider:
        case Provider.Hetzner:
            if not os.environ.get("TF_VAR_hcloud_token"):
                log.info("TF_VAR_hcloud_token not found in environment")
                log.info(
                    "Example: $ export TF_VAR_hcloud_token=$(bw get password <pw_id>)"
                )
                log.info(
                    "How to get API key: https://docs.hetzner.com/cloud/api/getting-started/generating-api-token/"
                )
                api_token = ask("Hetzner Cloud API token", PromptType.HIDDEN, None)
                os.environ["TF_VAR_hcloud_token"] = api_token


def generate_hetzner_config(
    config: Config,
    location: str | None,
    ssh_pubkeys: list[str],
    machines: list[ArgMachine],
) -> None:
    servers: list[dict[str, Any]] = []

    allowed_locations = {
        "nbg1": "DE: Nuremberg",
        "fsn1": "DE: Falkenstein",
        "hel1": "FIN: Helsinki",
        "ash": "US: Ashburn",
        "hil": "US: Hillsboro",
        "sin": "SG: Singapore",
    }
    allowed_os_images = {
        "ubuntu-24.04",
        "fedora-42",
        "debian-12",
        "centos-10",
    }

    if location is None:
        location = "nbg1"
        log.info(f"No location specified. Using default location: {location}")

    if location not in allowed_locations:
        msg = f"Invalid location: {location}. Valid locations: {json.dumps(allowed_locations, indent=2)}"
        raise VmSpawnError(msg)

    existing_machine_names = hetzner.get_hetzner_server_names(
        os.environ["TF_VAR_hcloud_token"]
    )
    for machine in machines:
        mname = machine["name"]
        index = 0
        if mname in existing_machine_names:
            new_machine = f"{mname}-{index}"
            log.warning(f"Machine name '{mname}' already exists.")
            while new_machine in existing_machine_names:
                new_machine = f"{mname}-{index}"
                index += 1

            mname = new_machine
            log.info(f"Renaming machine to '{new_machine}' to avoid conflict.")

        if machine["arch"] == "x86_64":
            server_type = "cpx11"
        elif machine["arch"] == "aarch64":
            server_type = "cax11"
        else:
            msg = f"Invalid architecture: {machine['arch']}. Valid architectures: x86_64, aarch64"
            raise VmSpawnError(msg)

        os_image = machine.get("os_image")
        if os_image is None:
            os_image = "ubuntu-24.04"
            log.info(f"No OS image specified. Using default OS image: {os_image}")
        if os_image not in allowed_os_images:
            msg = f"Invalid OS image: {os_image}. Valid OS images: {json.dumps(allowed_os_images, indent=2)}"
            raise VmSpawnError(msg)

        servers.append(
            {
                "name": mname,
                "location": location,
                "server_type": server_type,
                "ipv4": None,
                "ipv6": None,
                "os_image": os_image,
                "arch": machine["arch"],
            }
        )
    tr_write_vars(
        config,
        {
            "ssh_pubkeys": ssh_pubkeys,
            "servers": servers,
        },
    )


def tr_create(
    config: Config,
    provider: Provider,
    location: str | None,
    machines: list[ArgMachine],
) -> None:
    tr_ask_for_api_key(provider)
    tr_init(config, provider)
    ssh_pubkeys = [key.public.read_text() for key in config.ssh_keys]

    match provider:
        case Provider.Hetzner:
            generate_hetzner_config(
                config,
                location,
                ssh_pubkeys,
                machines,
            )
        case _:
            msg = f"Provider {provider} not implemented yet"
            raise NotImplementedError(msg)

    subprocess.run(
        ["tofu", f"-chdir={config.tr_dir}", "apply", "-auto-approve"],
        check=True,
    )


def tr_destroy(config: Config, provider: Provider) -> None:
    tr_ask_for_api_key(provider)
    subprocess.run(
        ["tofu", f"-chdir={config.tr_dir}", "destroy", "-auto-approve"],
        check=False,
    )
    tr_clean(config)
    log.info("Resources destroyed")
