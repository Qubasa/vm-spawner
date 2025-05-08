import logging

from vm_spawner import cli
from vm_spawner.errors import VmSpawnError

log = logging.getLogger(__name__)


def main() -> None:
    try:
        cli.run_cli()
    except VmSpawnError as e:
        if log.isEnabledFor(logging.DEBUG):
            raise
        log.error(e)
        exit(1)
