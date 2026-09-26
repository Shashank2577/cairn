"""Errors raised by the platform service. Each carries the HTTP status the web layer answers with."""
from __future__ import annotations


class PlatformError(Exception):
    status = 400


class InvalidInput(PlatformError):
    status = 400


class AuthError(PlatformError):
    """Credentials are missing or wrong."""
    status = 401


class PermissionDenied(PlatformError):
    status = 403


class AccountDisabled(PermissionDenied):
    """The password is right but an admin switched the account off. Only raised after the password checks out,
    so it tells nothing to someone who doesn't know it."""
    MESSAGE = "This account is disabled. Ask an admin to turn it back on."

    def __init__(self, message: str = MESSAGE):
        super().__init__(message)


class NotFound(PlatformError):
    status = 404


class Conflict(PlatformError):
    status = 409


class Expired(PlatformError):
    status = 410


class GitError(PlatformError):
    """A clone or refresh of a managed repository failed."""
    status = 502
