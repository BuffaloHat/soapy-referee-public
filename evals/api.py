"""JSON over HTTP to the tools' REST APIs, and fuse's credentials.

Shared by the modules that talk to the tools after a run (`scores`, `judge`)
and the one that sets up before it (`dataset`), so none of them has to import
another for a helper.
"""

import base64
import json
import os
import urllib.error
import urllib.request


def http(method: str, url: str, headers: dict, body=None) -> tuple[int, str]:
    request = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()[:300]


def fuse() -> tuple[str, dict]:
    """fuse's base URL and auth header, from the environment."""
    host = os.environ["LANGFUSE_HOST"].rstrip("/")
    keys = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
    return host, {"Authorization": f"Basic {base64.b64encode(keys.encode()).decode()}"}
