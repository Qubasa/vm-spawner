#!/usr/bin/env python3

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Define the base URL for the Hetzner Cloud API v1
HETZNER_API_BASE_URL = "https://api.hetzner.cloud/v1"


def get_hetzner_server_names(api_token: str) -> list[str]:
    """
    Connects to the Hetzner Cloud API using only Python standard libraries
    to return a list of server names in the corresponding project.

    Handles API pagination to retrieve all servers.

    Args:
        api_token: Your Hetzner Cloud API token for the specific project.

    Returns:
        A list of server names (strings).
        Returns an empty list if no servers are found or in case of an error.
    """
    all_server_names: list[str] = []
    page = 1
    per_page = 50  # Max allowed by Hetzner API per page
    endpoint = f"{HETZNER_API_BASE_URL}/servers"

    # Prepare headers for authentication and content type
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",  # Indicate we expect JSON back
        "User-Agent": "Python-urllib/3",  # Good practice to identify client
    }

    # Optional: Create a default SSL context for HTTPS robustness
    # This often helps avoid certificate verification issues on some systems
    ssl_context = ssl.create_default_context()

    while True:
        # Prepare query parameters for pagination
        params = {
            "page": page,
            "per_page": per_page,
        }
        query_string = urllib.parse.urlencode(params)
        full_url = f"{endpoint}?{query_string}"

        try:
            # Create a request object
            req = urllib.request.Request(full_url, headers=headers, method="GET")

            # Make the request and open the URL
            with urllib.request.urlopen(
                req, context=ssl_context, timeout=30
            ) as response:
                # Check if the request was successful (status code 200-299)
                if not (200 <= response.status < 300):
                    print(f"HTTP Error: {response.status} {response.reason}")
                    # Attempt to read error body for more details
                    try:
                        error_body = response.read().decode("utf-8", errors="ignore")
                        print(f"Error details: {error_body}")
                    except Exception:
                        pass  # Ignore if reading error body fails
                    break  # Exit loop on error

                # Read the response body (bytes) and decode it (to string)
                response_body_bytes = response.read()
                response_body_str = response_body_bytes.decode(
                    "utf-8"
                )  # API uses UTF-8

                # Parse the JSON response string into a Python dictionary
                data: dict[str, Any] = json.loads(response_body_str)

                # Extract server names from the current page
                servers_on_page: list[dict[str, Any]] = data.get("servers", [])
                if not servers_on_page:
                    break  # No servers found on this page (might be end)

                for server in servers_on_page:
                    if "name" in server:
                        all_server_names.append(server["name"])

                # Check pagination metadata to see if there's a next page
                pagination_info: dict[str, Any] | None = data.get("meta", {}).get(
                    "pagination"
                )
                if pagination_info and pagination_info.get("next_page"):
                    page = pagination_info[
                        "next_page"
                    ]  # API tells us the next page number
                    # print(f"Fetching next page: {page}") # Optional: for debugging
                else:
                    break  # No more pages

        except urllib.error.HTTPError as http_err:
            # Handle HTTP errors (e.g., 401 Unauthorized, 404 Not Found) raised by urlopen
            print(f"HTTP error occurred: {http_err.code} {http_err.reason}")
            try:
                # Try reading the error response body if available
                error_content = http_err.read().decode("utf-8", errors="ignore")
                print(f"Error content: {error_content}")
            except Exception:
                print("(Could not read error response body)")
            break  # Exit loop on error
        except urllib.error.URLError as url_err:
            # Handle network/connection errors (e.g., DNS failure, connection refused)
            print(f"URL error occurred: {url_err.reason}")
            break  # Exit loop on error
        except json.JSONDecodeError as json_err:
            print(f"Error decoding JSON response: {json_err}")
            # It might be helpful to see the raw response that failed parsing
            # print(f"Raw response text: {response_body_str}")
            break  # Exit loop on error
        except Exception as e:
            # Catch any other unexpected errors
            print(f"An unexpected error occurred: {e}")
            import traceback

            traceback.print_exc()  # Print full traceback for unexpected errors
            break  # Exit loop on error

    return all_server_names


# --- Example Usage ---
if __name__ == "__main__":
    # Replace "YOUR_API_TOKEN" with the actual API token you generated
    # It's HIGHLY recommended to load this from a secure location
    # (environment variable, config file, secrets manager)
    # rather than hardcoding it.
    hetzner_api_token = os.environ.get("TF_VAR_hcloud_token", "YOUR_API_TOKEN")

    if hetzner_api_token == "YOUR_API_TOKEN":
        print("Please replace 'YOUR_API_TOKEN' with your actual Hetzner API token.")
    else:
        names = get_hetzner_server_names(hetzner_api_token)

        if names:
            print("Found the following server names:")
            for name in names:
                print(f"- {name}")
        else:
            print("No servers found or an error occurred during retrieval.")
