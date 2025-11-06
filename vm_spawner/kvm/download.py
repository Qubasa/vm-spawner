# ruff: noqa: TRY301 TRY300

import contextlib
import hashlib
import logging
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


def _verify_checksum(file_path: Path, expected_checksum: str) -> None:
    """
    Verifies the SHA256 checksum of a file.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: If the file cannot be read.
        ValueError: If the checksum does not match.
    """
    log.info(f"Verifying SHA256 checksum for {file_path}...")
    hasher = hashlib.sha256()
    try:
        with file_path.open("rb") as f:
            while True:
                chunk = f.read(65536)  # Read in 64k chunks
                if not chunk:
                    break
                hasher.update(chunk)
        calculated_checksum = hasher.hexdigest()
        expected_lower = expected_checksum.lower()
        if calculated_checksum == expected_lower:
            log.info(f"Checksum OK: {calculated_checksum}")
        else:
            msg = (
                f"Checksum mismatch for {file_path}!\n"
                f"  Expected: {expected_lower}\n"
                f"  Got:      {calculated_checksum}"
            )
            log.error(msg)
            raise ValueError(msg)
    except FileNotFoundError:
        log.error(f"File not found for checksum verification: {file_path}")
        raise
    except OSError as e:
        log.error(f"Error reading file {file_path} for checksum: {e}", exc_info=True)
        msg = f"Could not read file {file_path} for checksum"
        raise RuntimeError(msg) from e


def download_file(url: str, destination: Path, checksum: str | None = None) -> None:
    """
    Downloads a file from a URL to a destination path using urllib,
    or copies it if the URL is a local file path.
    Optionally verifies its SHA256 checksum.

    Raises:
        ValueError: If checksum verification fails.
        urllib.error.URLError: For network errors during download.
        urllib.error.HTTPError: For HTTP errors (like 404).
        OSError: For file system errors during write.
        RuntimeError: For other unexpected errors or read errors during verification.
        FileNotFoundError: If the source local file doesn't exist.
    """
    if destination.exists():
        log.info(f"File {destination} already exists.")
        if checksum:
            try:
                _verify_checksum(destination, checksum)
                log.info("Existing file checksum matches. Skipping download.")
                return  # Success, file exists and is valid
            except (ValueError, RuntimeError, FileNotFoundError) as e:
                log.warning(
                    f"Existing file {destination} failed verification ({e}). Re-downloading..."
                )
                with contextlib.suppress(OSError):
                    destination.unlink()  # Remove corrupted/wrong file
        else:
            log.info("No checksum provided, using existing file.")
            return  # Success, file exists

    # Detect if the "url" is actually a local file path
    is_local_file = False
    source_path = None

    # Check if it looks like a local path (starts with / or ./ or ../)
    if url.startswith('/') or url.startswith('./') or url.startswith('../'):
        source_path = Path(url)
        is_local_file = True
    # Also check if it's a valid Path object that exists
    elif not any(url.startswith(proto) for proto in ['http://', 'https://', 'ftp://', 'file://']):
        # Try treating it as a local path
        source_path = Path(url)
        if source_path.exists():
            is_local_file = True

    # Handle local file copy
    if is_local_file and source_path:
        if not source_path.exists():
            msg = f"Source file does not exist: {source_path}"
            log.error(msg)
            raise FileNotFoundError(msg)

        if not source_path.is_file():
            msg = f"Source path is not a file: {source_path}"
            log.error(msg)
            raise ValueError(msg)

        log.info(f"Copying local file {source_path} to {destination}...")
        try:
            # Ensure parent directory exists
            destination.parent.mkdir(parents=True, exist_ok=True)

            # Copy the file
            shutil.copy2(source_path, destination)
            log.info(f"File copied successfully to {destination}")

            # Verify checksum if provided
            if checksum:
                _verify_checksum(destination, checksum)

            return  # Success

        except OSError as e:
            log.error(f"Error copying file from {source_path} to {destination}: {e}", exc_info=True)
            # Clean up potentially partial file
            with contextlib.suppress(OSError):
                if destination.exists():
                    destination.unlink()
            raise

    # Handle URL download
    log.info(f"Downloading {url} to {destination}...")
    try:
        # Ensure parent directory exists
        destination.parent.mkdir(parents=True, exist_ok=True)

        req = urllib.request.Request(
            url, headers={"User-Agent": "vm-spawner/1.0"}
        )  # More specific agent
        with (
            urllib.request.urlopen(req, timeout=300) as response,
            destination.open("wb") as f,
        ):
            # Check status *before* reading - urlopen might raise HTTPError for >=400 already
            # but this is an extra check.
            if response.status >= 400:
                msg = f"HTTP Error {response.status} {response.reason} for URL {url}"
                log.error(msg)
                # Re-raise as HTTPError for consistency if urlopen didn't already
                raise urllib.error.HTTPError(
                    url, response.status, response.reason, response.headers, None
                )

            content_length_str = response.getheader("Content-Length")
            total_size = 0
            if content_length_str:
                with contextlib.suppress(ValueError, TypeError):
                    total_size = int(content_length_str)
            if total_size <= 0:
                log.warning("Could not determine Content-Length or it was zero.")

            downloaded_size = 0
            start_time = time.time()
            chunk_size = 1024 * 1024  # 1MB chunk

            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded_size += len(chunk)

                # Progress reporting (optional, can be removed if too verbose)
                percent_str = (
                    f" ({downloaded_size / total_size * 100:.1f}%)"
                    if total_size > 0
                    else ""
                )
                elapsed_time = time.time() - start_time
                speed = (
                    (downloaded_size / elapsed_time / 1024 / 1024)
                    if elapsed_time > 0
                    else 0.0
                )
                # Progress logging (debug level to avoid clutter)
                log.debug(
                    f"Downloaded {downloaded_size / 1024 / 1024:.2f}"
                    f"{f' / {total_size / 1024 / 1024:.2f}' if total_size > 0 else ''} MB"
                    f"{percent_str} at {speed:.2f} MB/s"
                )
            log.info("Download complete.")

    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        log.error(f"Network error downloading {url}: {e}", exc_info=True)
        # Clean up potentially partial file
        with contextlib.suppress(OSError):
            if destination.exists():
                destination.unlink()
        raise  # Re-raise the specific network error
    except OSError as e:
        log.error(f"File system error writing to {destination}: {e}", exc_info=True)
        # Clean up potentially partial file
        with contextlib.suppress(OSError):
            if destination.exists():
                destination.unlink()
        raise  # Re-raise the file system error
    except Exception as e:
        log.exception(f"An unexpected error occurred during download to {destination}")
        # Clean up potentially partial file
        with contextlib.suppress(OSError):
            if destination.exists():
                destination.unlink()
        msg = f"Unexpected download error for {url}"
        raise RuntimeError(msg) from e

    # Verify checksum *after* successful download if provided
    if checksum:
        try:
            _verify_checksum(destination, checksum)
        except (ValueError, RuntimeError, FileNotFoundError):
            log.error(
                f"Checksum verification failed after download for {destination}. Deleting file.",
                exc_info=False,
            )
            with contextlib.suppress(OSError):
                destination.unlink()
            raise  # Re-raise the verification error
