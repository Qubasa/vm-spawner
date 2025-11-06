#!/usr/bin/env python3

# ruff: noqa: TRY301 TRY300

import logging
import shlex  # For safer command printing if needed later
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


class RemoteCommandError(RuntimeError):
    """Custom exception for remote command failures."""

    def __init__(
        self,
        message: str,
        command: list[str],
        returncode: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self) -> str:
        # Extract just the actual command (after '--')
        try:
            dash_index = self.command.index('--')
            actual_command = ' '.join(map(shlex.quote, self.command[dash_index + 1:]))
        except (ValueError, IndexError):
            actual_command = ' '.join(map(shlex.quote, self.command))

        details = f"\n{'=' * 80}\nCommand failed: {actual_command}"
        if self.returncode is not None:
            details += f"\nExit code: {self.returncode}"
        if self.stderr:
            # Filter common SSH noise
            filtered_stderr = "\n".join(
                line
                for line in self.stderr.splitlines()
                if "Pseudo-terminal will not be allocated" not in line
                and "Warning: Permanently added" not in line
            )
            if filtered_stderr.strip():
                details += f"\n\nError output:\n{filtered_stderr}"
        if self.stdout and self.stdout.strip():
            details += f"\n\nStandard output:\n{self.stdout}"
        details += f"\n{'=' * 80}"
        return details


@dataclass
class RemoteCommandResult:
    """Class to hold the result of a remote command execution."""

    stdout: str
    stderr: str
    returncode: int


def run_remote_command(
    host: str,
    command: list[str],
    *,
    timeout: int = 60,
    check: bool = True,
    ssh_key: Path | None = None,
) -> RemoteCommandResult:
    """
    Runs a command on the remote host via SSH.

    Returns:
        The stdout of the command, stripped of leading/trailing whitespace.
    Raises:
        RemoteCommandError: If the command fails (non-zero exit code), times out,
                          or encounters other execution errors.
        FileNotFoundError: If the 'ssh' command is not found locally.
    """
    ssh_cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
    ]

    if ssh_key:
        ssh_cmd += [
            "-i",
            str(ssh_key),  # Use the provided SSH key
        ]

    ssh_cmd += [
        "-T",  # Disable pseudo-terminal allocation
        host,
        "--",
        *command,
    ]

    # Show just the actual command being run, not the full SSH wrapper
    actual_cmd = ' '.join(map(shlex.quote, command))
    log.info(f"→ Running on {host}: {actual_cmd}")

    try:
        result = subprocess.run(
            ssh_cmd, check=check, capture_output=True, text=True, timeout=timeout
        )

        # Only log output if it's not empty and relevant
        if result.stdout.strip():
            log.debug("Command output:\n%s", result.stdout)

        if result.stderr:
            # Filter common SSH noise before logging
            filtered_stderr = "\n".join(
                line
                for line in result.stderr.splitlines()
                if "Pseudo-terminal will not be allocated" not in line
                and "Warning: Permanently added" not in line
            )
            if filtered_stderr.strip():
                log.warning("Command stderr:\n%s", filtered_stderr)

        log.debug("✓ Command completed successfully")
        return RemoteCommandResult(
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            returncode=result.returncode,
        )
    except subprocess.CalledProcessError as e:
        msg = f"Remote command failed with exit code {e.returncode}"
        raise RemoteCommandError(
            msg,
            command=ssh_cmd,
            returncode=e.returncode,
            stdout=e.stdout,
            stderr=e.stderr,
        ) from e
    except subprocess.TimeoutExpired as e:
        msg = f"Remote command timed out after {timeout}s"
        raise RemoteCommandError(msg, command=ssh_cmd) from e
    except FileNotFoundError:
        # Specifically catch if 'ssh' isn't found locally
        log.error("The 'ssh' command was not found locally.", exc_info=False)
        raise  # Re-raise the original FileNotFoundError
    except Exception as e:
        # Catch other potential errors like permission denied for ssh key etc.
        log.exception("An unexpected error occurred running remote command.")
        msg = f"An unexpected error occurred running remote command: {e}"
        raise RemoteCommandError(msg, command=ssh_cmd) from e
