"""Custom exceptions for the Ninebot integration."""

from __future__ import annotations


class NinebotError(Exception):
    """Base exception for Ninebot errors."""


class NinebotAuthError(NinebotError):
    """Raised when authentication fails."""


class NinebotApiError(NinebotError):
    """Raised when the upstream API returns an unexpected response."""


class NinebotConnectionError(NinebotError):
    """Raised when a network call fails."""
