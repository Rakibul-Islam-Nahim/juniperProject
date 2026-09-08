"""IPAM HTTP client package.

The Backend Agent no longer talks to the IPAM database directly. It uses this
httpx-based client to call the standalone `ipam-service` over HTTP.
"""
from app.ipam_client.client import IPAMClient, IPAMError, IPAMExhausted, IPAMUnavailable

__all__ = ["IPAMClient", "IPAMError", "IPAMExhausted", "IPAMUnavailable"]