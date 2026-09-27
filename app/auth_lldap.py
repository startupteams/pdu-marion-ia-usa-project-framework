#!/usr/bin/env python3
"""
LLDAP authentication + central authorization for PDU Manager (VM154).

Auth model:
  - AI/API: HTTP Basic (LLDAP username/password) -> bind-check against LLDAP :3890
  - UI:     session-based (same bind-check), plus emergency local root fallback
  - Groups: member=<userDN> search on ou=groups (LLDAP exposes no memberOf)

Authorization (central, per V3 plan section 8):
  can_view               pdu-viewer OR pdu-operator OR pdu-admin
  can_control_normal     pdu-operator OR pdu-admin
  can_override_protected pdu-admin OR (pdu-ai-agent AND pdu-operator AND pdu-ai-admin-override)
  can_administer_manager pdu-admin only
  emergency-local root   == pdu-admin (web UI only)
"""

import base64
import json
import os
import threading
import time
from datetime import datetime, timezone

from ldap3 import Server, Connection

# --- configuration (from the app's secrets.env, loaded by the main app) ------

LDAP_SERVER = "10.0.20.101"
LDAP_PORT = 3890
LDAP_BASE = "dc=example,dc=com"
GROUP_SEARCH_TIMEOUT = 8
GROUP_CACHE_TTL_SECONDS = 60  # V3 section 9.2; configurable via env


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


GROUP_CACHE_TTL_SECONDS = _env_int("PDU_LDAP_GROUP_CACHE_TTL", GROUP_CACHE_TTL_SECONDS)


class LdapError(Exception):
    """LDAP communication failure (distinct from invalid credentials)."""


class LdapUnavailable(LdapError):
    """LDAP server unreachable - emergency-local path must be offered."""


# ----------------------------------------------------------------------------
# Credential helpers (bind DN + service bind user)
# ----------------------------------------------------------------------------

def _load_env_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key] = value.strip()
    except FileNotFoundError:
        return {}
    return values


def _b64(values, name):
    encoded = values.get(f"{name}_B64", "")
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8")


def load_service_bind():
    """Returns (bind_dn, bind_password) for the PDU Manager's LDAP service account."""
    env = _load_env_file("/etc/pdu-control/secrets.env")
    user = _b64(env, "LDAP_SERVICE_USER")
    password = _b64(env, "LDAP_SERVICE_PASS")
    if not user or not password:
        raise LdapUnavailable("LDAP service credentials are not configured.")
    return f"uid={user},ou=people,{LDAP_BASE}", password


# ----------------------------------------------------------------------------
# Bind-check + group lookup
# ----------------------------------------------------------------------------

def verify_credentials(username, password):
    """
    Bind as uid=<username> with the supplied password.

    Returns True/False for valid/invalid credentials.
    Raises LdapUnavailable if the server cannot be reached at all.
    """
    if not username or not password:
        return False
    user_dn = f"uid={username},ou=people,{LDAP_BASE}"
    try:
        server = Server(LDAP_SERVER, port=LDAP_PORT, use_ssl=False, get_info=None,
                        connect_timeout=GROUP_SEARCH_TIMEOUT)
        conn = Connection(server, user=user_dn, password=password,
                          auto_bind=True, receive_timeout=GROUP_SEARCH_TIMEOUT)
    except Exception as exc:
        message = str(exc).lower()
        if "invalid" in message or "inappropriate" in message or "unwilling" in message \
                or "no such object" in message or "invalidcredentials" in message:
            return False
        raise LdapUnavailable(f"LDAP bind failed: {exc}") from exc
    try:
        conn.unbind()
    except Exception:
        pass
    return True


def groups_for(username):
    """Group CNs for a user via member=<dn> search (no memberOf in LLDAP)."""
    bind_dn, bind_password = load_service_bind()
    user_dn = f"uid={username},ou=people,{LDAP_BASE}"
    try:
        server = Server(LDAP_SERVER, port=LDAP_PORT, use_ssl=False, get_info=None,
                        connect_timeout=GROUP_SEARCH_TIMEOUT)
        conn = Connection(server, user=bind_dn, password=bind_password,
                          auto_bind=True, receive_timeout=GROUP_SEARCH_TIMEOUT)
    except Exception as exc:
        raise LdapUnavailable(f"LDAP service bind failed: {exc}") from exc
    try:
        conn.search(
            f"ou=groups,{LDAP_BASE}",
            f"(member={user_dn})",
            attributes=["cn"],
        )
        groups = []
        for entry in conn.entries:
            try:
                groups.append(str(entry.cn.value))
            except Exception:
                continue
        return groups
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Cached actor lookup
# ----------------------------------------------------------------------------

_cache_lock = threading.Lock()
_actor_cache = {}  # username -> {"groups": [...], "expires": epoch}


def actor_groups(username, force_refresh=False):
    """Group list with a short TTL cache so revocation lands within ~60s."""
    now = time.time()
    with _cache_lock:
        cached = _actor_cache.get(username)
        if cached and not force_refresh and cached["expires"] > now:
            return list(cached["groups"])
    fetched = groups_for(username)
    with _cache_lock:
        _actor_cache[username] = {"groups": list(fetched), "expires": now + GROUP_CACHE_TTL_SECONDS}
    return list(fetched)


def drop_cached_groups(username=None):
    with _cache_lock:
        if username is None:
            _actor_cache.clear()
        else:
            _actor_cache.pop(username, None)


# ----------------------------------------------------------------------------
# Central authorization (V3 plan section 8)
# ----------------------------------------------------------------------------

class Actor:
    def __init__(self, username, groups, auth_source):
        self.username = username
        self.groups = list(groups)
        self.auth_source = auth_source  # "lldap" or "emergency-local"
        self._set = set(self.groups)

    def as_dict(self):
        return {
            "username": self.username,
            "auth_source": self.auth_source,
            "groups": self.groups,
            "can_view": self.can_view(),
            "can_control": self.can_control_normal(),
            "can_admin_override": self.can_override_protected(),
            "can_administer_manager": self.can_administer_manager(),
        }

    def can_view(self):
        if self.auth_source == "emergency-local":
            return True
        return bool(self._set & {"pdu-viewer", "pdu-operator", "pdu-admin"})

    def can_control_normal(self):
        if self.auth_source == "emergency-local":
            return True
        return bool(self._set & {"pdu-operator", "pdu-admin"})

    def can_override_protected(self):
        if self.auth_source == "emergency-local":
            return True
        if "pdu-admin" in self._set:
            return True
        return bool({"pdu-ai-agent", "pdu-operator", "pdu-ai-admin-override"} <= self._set)

    def can_administer_manager(self):
        if self.auth_source == "emergency-local":
            return True
        return "pdu-admin" in self._set


EMERGENCY_ACTOR = None  # set by the app at startup


def emergency_actor():
    global EMERGENCY_ACTOR
    if EMERGENCY_ACTOR is None:
        EMERGENCY_ACTOR = Actor("root", ["pdu-admin"], "emergency-local")
    return EMERGENCY_ACTOR


def authenticate_lldap(username, password, force_refresh=False):
    """
    Full auth for API + UI login: verify password, then load groups.

    Returns Actor or None (invalid credentials).
    Raises LdapUnavailable when LDAP cannot be reached.
    """
    if not verify_credentials(username, password):
        return None
    groups = actor_groups(username, force_refresh=force_refresh)
    return Actor(username, groups, "lldap")
