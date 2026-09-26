"""Teams, projects, roles and tokens: what lets one Cairn server serve a whole team (or quietly stay out of
the way for a solo developer).

State lives in ``$CAIRN_HOME/platform.db`` (default ``~/.cairn``). ``service.Platform`` is the API;
``web`` wires it into FastAPI; ``commands`` holds the terminal commands.
"""
from .config import LOCAL, TEAM, ServerConfig, cairn_home
from .errors import (AccountDisabled, AuthError, Conflict, Expired, GitError, InvalidInput, NotFound,
                     PermissionDenied, PlatformError)
from .models import (ApiToken, AuditEntry, Invitation, Member, ProjectRecord, Team, User, WebhookResult,
                     WebSession)
from .rbac import ACTIONS, MATRIX, ROLES, SCOPE_PRESETS, Principal, normalize_scopes, role_allows
from .service import Platform

__all__ = [
    "ACTIONS", "LOCAL", "MATRIX", "ROLES", "SCOPE_PRESETS", "TEAM",
    "AccountDisabled", "ApiToken", "AuditEntry", "AuthError", "Conflict", "Expired", "GitError", "InvalidInput", "Invitation",
    "Member", "NotFound", "PermissionDenied", "Platform", "PlatformError", "Principal", "ProjectRecord",
    "ServerConfig", "Team", "User", "WebSession", "WebhookResult", "cairn_home", "normalize_scopes",
    "role_allows",
]
