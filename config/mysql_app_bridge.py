"""
PyMySQL connection to Beanthentic-App MySQL on the LAN (XAMPP device).
Same bridge as Beanthentic admin web — see Beanthentic-App/xampp-enable-lan-mysql.sql on the app PC.
"""

from __future__ import annotations

import os

import pymysql
from pymysql.err import OperationalError


def _is_loopback(h: str) -> bool:
    x = (h or "").strip().lower()
    return x in ("127.0.0.1", "localhost", "::1")


def connect_app_mysql(params: dict) -> pymysql.connections.Connection:
    timeout_raw = os.getenv("BEANTHENTIC_APP_DB_CONNECT_TIMEOUT", "10").strip()
    try:
        connect_timeout = max(2, min(60, int(timeout_raw)))
    except ValueError:
        connect_timeout = 10

    host = str(params.get("host") or "").strip()
    if not host:
        raise OperationalError(
            2003, "app_db_host is empty — set the XAMPP device LAN IP in settings.json"
        )

    base = {**params, "host": host, "connect_timeout": connect_timeout}

    failover_raw = os.getenv("BEANTHENTIC_APP_DB_FAILOVER_LOCALHOST", "0").strip().lower()
    failover = failover_raw in ("1", "true", "yes", "on")

    try:
        return pymysql.connect(**base)
    except OperationalError as e:
        errno = e.args[0] if e.args else None
        if failover and errno == 2003 and not _is_loopback(host):
            return pymysql.connect(**{**base, "host": "127.0.0.1"})
        raise
