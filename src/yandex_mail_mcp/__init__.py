"""Yandex Mail tools for MCP and optional inbox monitoring."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yandex-mail-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0"
