# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Entry point for the MCP server."""

import os

from ads_mcp.coordinator import mcp

# The following imports are necessary to register the resources with the `mcp`
# object, even though they are not directly used in this file.
# Tools are loaded dynamically via reflection in coordinator.py.
# The `# noqa: F401` comment tells the linter to ignore the "unused import"
# warning.
from ads_mcp.resources import (
    discovery,
    metrics,
    release_notes,
    segments,
)  # noqa: F401


def run_server() -> None:
    _CLIENT_ID = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID")
    _CLIENT_SECRET = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET")
    if bool(_CLIENT_ID) != bool(_CLIENT_SECRET):
        raise ValueError("Configure both OAuth client ID and client secret.")

    oauth_enabled = bool(_CLIENT_ID and _CLIENT_SECRET)
    transport = os.environ.get("GOOGLE_ADS_MCP_TRANSPORT") or (
        "streamable-http" if oauth_enabled else "stdio"
    )
    if transport not in ("stdio", "streamable-http"):
        raise ValueError(
            "GOOGLE_ADS_MCP_TRANSPORT must be stdio or streamable-http."
        )
    if transport == "stdio" and oauth_enabled:
        raise ValueError("OAuth requires the streamable-http transport.")

    if transport == "streamable-http":
        port = int(os.environ.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        host = os.environ.get("GOOGLE_ADS_MCP_HOST") or (
            "0.0.0.0" if oauth_enabled else "127.0.0.1"
        )
        mcp.run(
            transport="streamable-http",
            port=port,
            host=host,
            uvicorn_config={"access_log": False},
        )
    else:
        mcp.run()


if __name__ == "__main__":
    run_server()
