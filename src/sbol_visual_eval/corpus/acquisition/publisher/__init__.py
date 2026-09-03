"""Acquisition of ACS Version-of-Record PDFs through an authenticated session.

Two paths share one bridge: a curl-cffi client that reuses the browser's
EZProxy cookie, and a loopback bridge fed by a browser-side runner so that
credentials never leave the browser.
"""

from .authenticated import acquire_publisher_authenticated
from .bridge import PublisherBridge, serve_publisher_bridge
from .urls import PUBLISHER_ORIGIN

__all__ = [
    "PUBLISHER_ORIGIN",
    "PublisherBridge",
    "acquire_publisher_authenticated",
    "serve_publisher_bridge",
]
