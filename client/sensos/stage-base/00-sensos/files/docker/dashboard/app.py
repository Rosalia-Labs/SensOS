#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Rosalia Labs LLC

import logging
import os
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

import psycopg
from flask import Flask, Response, jsonify, render_template, request
from psycopg import sql
from psycopg.rows import dict_row


def env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("dashboard")

WINDOW_HOURS = env_int("DASHBOARD_WINDOW_HOURS", 24)
MAX_POINTS = env_int("DASHBOARD_MAX_POINTS", 360)
REFRESH_SEC = env_int("DASHBOARD_REFRESH_SEC", 30)
I2C_KEYS = [
    k.strip()
    for k in os.environ.get(
        "DASHBOARD_I2C_KEYS",
        "temperature_c,humidity_percent,pressure_hpa,co2_ppm,lux",
    ).split(",")
    if k.strip()
]

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "sensos")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "change-me")

DB_NAME = os.environ.get("POSTGRES_DB", "postgres")
DB_HOST = os.environ.get("DB_HOST", "sensos-client-database")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_ADMIN_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_ADMIN_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "sensos")
DB_READONLY_USER = os.environ.get("DASHBOARD_DB_USER", "sensos_dashboard_ro")
DB_READONLY_PASSWORD = os.environ.get(
    "DASHBOARD_DB_PASSWORD", "sensos_dashboard_readonly"
)
BOOTSTRAP_DB_ROLE = env_bool("DASHBOARD_DB_BOOTSTRAP", True)

app = Flask(__name__)
_bootstrap_attempted = False


def _unauthorized() -> Response:
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="SensOS Dashboard"'},
    )


def _authorized() -> bool:
    auth = request.authorization
    if not auth:
        return False
    return secrets.compare_digest(auth.username or "", DASHBOARD_USER) and secrets.compare_digest(
        auth.password or "", DASHBOARD_PASSWORD
    )


@app.before_request
def require_basic_auth():
    if request.path == "/healthz":
        return None
    if not _authorized():
        return _unauthorized()
    return None


@app.after_request
def add_security_headers(resp: Response):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
    )
    return resp


def _table_exists(cur: psycopg.Cursor, table_name: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'sensos' AND table_name = %s
        ) AS exists
        """,
        (table_name,),
    )
    row = cur.fetchone()
    return bool(row and row["exists"])


def _to_ms(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * 1000)


def _downsample(points: List[Dict[str, Any]], max_points: int) -> List[Dict[str, Any]]:
    if len(points) <= max_points:
        return points
    stride = len(points) / float(max_points)
    out = []
    idx = 0.0
    while int(idx) < len(points):
        out.append(points[int(idx)])
        idx += stride
    if out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


def _bootstrap_readonly_role() -> None:
    logger.info("Ensuring read-only dashboard role exists...")
    with psycopg.connect(
        dbname=DB_NAME,
        user=DB_ADMIN_USER,
        password=DB_ADMIN_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        row_factory=dict_row,
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS sensos")
            try:
                cur.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(
                        sql.Identifier(DB_READONLY_USER)
                    ),
                    (DB_READONLY_PASSWORD,),
                )
            except psycopg.errors.DuplicateObject:
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(
                        sql.Identifier(DB_READONLY_USER)
                    ),
                    (DB_READONLY_PASSWORD,),
                )

            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(DB_NAME), sql.Identifier(DB_READONLY_USER)
                )
            )
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA sensos TO {}").format(
                    sql.Identifier(DB_READONLY_USER)
                )
            )
            cur.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA sensos TO {}").format(
                    sql.Identifier(DB_READONLY_USER)
                )
            )
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA sensos "
                    "GRANT SELECT ON TABLES TO {}"
                ).format(
                    sql.Identifier(DB_ADMIN_USER), sql.Identifier(DB_READONLY_USER)
                )
            )
    logger.info("Read-only dashboard role is ready.")


def _get_conn() -> psycopg.Connection:
    global _bootstrap_attempted
    try:
        conn = psycopg.connect(
            dbname=DB_NAME,
            user=DB_READONLY_USER,
            password=DB_READONLY_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            row_factory=dict_row,
        )
    except Exception as first_error:
        if not BOOTSTRAP_DB_ROLE or _bootstrap_attempted:
            raise
        try:
            _bootstrap_readonly_role()
            _bootstrap_attempted = True
            conn = psycopg.connect(
                dbname=DB_NAME,
                user=DB_READONLY_USER,
                password=DB_READONLY_PASSWORD,
                host=DB_HOST,
                port=DB_PORT,
                row_factory=dict_row,
            )
        except Exception:
            raise first_error

    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '10000ms'")
        cur.execute("SET default_transaction_read_only = on")
    return conn


def _fetch_dashboard_payload() -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": WINDOW_HOURS,
        "summary": {
            "active_files": None,
            "audio_hours": None,
            "segments_total": None,
            "segments_active": None,
            "detections_window": 0,
            "disk_free_gb": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "load_1m": None,
            "latest_system_at": None,
        },
        "detection_series": [],
        "system_series": {
            "disk_available_gb": [],
            "memory_used_pct": [],
            "load_1m": [],
        },
        "environment_series": {},
        "top_species": [],
        "errors": [],
    }

    with _get_conn() as conn:
        with conn.cursor() as cur:
            has_audio_files = _table_exists(cur, "audio_files")
            has_audio_segments = _table_exists(cur, "audio_segments")
            has_birdnet_scores = _table_exists(cur, "birdnet_scores")
            has_system_stats = _table_exists(cur, "system_stats")
            has_i2c = _table_exists(cur, "i2c_readings")

            if has_audio_files:
                cur.execute(
                    """
                    SELECT
                        COUNT(*)::bigint AS active_files,
                        COALESCE(SUM(frames::double precision / NULLIF(sample_rate, 0)) / 3600.0, 0.0) AS audio_hours
                    FROM sensos.audio_files
                    WHERE deleted IS NOT TRUE
                    """
                )
                row = cur.fetchone()
                if row:
                    payload["summary"]["active_files"] = int(row["active_files"] or 0)
                    payload["summary"]["audio_hours"] = round(
                        float(row["audio_hours"] or 0.0), 2
                    )

            if has_audio_segments:
                cur.execute(
                    """
                    SELECT
                        COUNT(*)::bigint AS segments_total,
                        COUNT(*) FILTER (WHERE zeroed IS NOT TRUE)::bigint AS segments_active
                    FROM sensos.audio_segments
                    """
                )
                row = cur.fetchone()
                if row:
                    payload["summary"]["segments_total"] = int(row["segments_total"] or 0)
                    payload["summary"]["segments_active"] = int(
                        row["segments_active"] or 0
                    )

                if has_audio_files:
                    cur.execute(
                        """
                        SELECT
                            date_trunc('hour', COALESCE(f.capture_timestamp, f.cataloged_at, s.created_at)) AS bucket,
                            COUNT(*)::int AS detections
                        FROM sensos.audio_segments s
                        JOIN sensos.audio_files f ON f.id = s.file_id
                        WHERE f.deleted IS NOT TRUE
                          AND COALESCE(f.capture_timestamp, f.cataloged_at, s.created_at)
                              >= NOW() - make_interval(hours => %s)
                        GROUP BY bucket
                        ORDER BY bucket ASC
                        """,
                        (WINDOW_HOURS,),
                    )
                    detection_points = [
                        {"t": _to_ms(r["bucket"]), "v": int(r["detections"])}
                        for r in cur.fetchall()
                        if r.get("bucket") is not None
                    ]
                    payload["detection_series"] = _downsample(detection_points, MAX_POINTS)
                    payload["summary"]["detections_window"] = sum(
                        p["v"] for p in detection_points
                    )

            if has_birdnet_scores and has_audio_segments and has_audio_files:
                cur.execute(
                    """
                    WITH recent_segments AS (
                        SELECT s.id
                        FROM sensos.audio_segments s
                        JOIN sensos.audio_files f ON f.id = s.file_id
                        WHERE f.deleted IS NOT TRUE
                          AND COALESCE(f.capture_timestamp, f.cataloged_at, s.created_at)
                              >= NOW() - make_interval(hours => %s)
                    ),
                    top_scores AS (
                        SELECT DISTINCT ON (b.segment_id)
                            b.segment_id, b.label, b.score
                        FROM sensos.birdnet_scores b
                        JOIN recent_segments rs ON rs.id = b.segment_id
                        ORDER BY b.segment_id, b.score DESC
                    )
                    SELECT
                        label,
                        COUNT(*)::int AS detections,
                        ROUND(AVG(score)::numeric, 3)::float8 AS avg_score
                    FROM top_scores
                    GROUP BY label
                    ORDER BY detections DESC
                    LIMIT 12
                    """,
                    (WINDOW_HOURS,),
                )
                payload["top_species"] = [
                    {
                        "label": r["label"],
                        "detections": int(r["detections"]),
                        "avg_score": float(r["avg_score"] or 0.0),
                    }
                    for r in cur.fetchall()
                ]

            if has_system_stats:
                cur.execute(
                    """
                    SELECT
                        recorded_at,
                        disk_available_gb,
                        memory_used_mb::double precision AS memory_used_mb,
                        CASE
                            WHEN memory_total_mb > 0 THEN
                                100.0 * memory_used_mb::double precision / memory_total_mb::double precision
                            ELSE NULL
                        END AS memory_used_pct,
                        load_1m
                    FROM sensos.system_stats
                    WHERE recorded_at >= NOW() - make_interval(hours => %s)
                    ORDER BY recorded_at ASC
                    """,
                    (WINDOW_HOURS,),
                )
                disk_series = []
                memory_series = []
                load_series = []
                latest = None
                for r in cur.fetchall():
                    ts = r["recorded_at"]
                    if ts is None:
                        continue
                    latest = r
                    t_ms = _to_ms(ts)
                    if r["disk_available_gb"] is not None:
                        disk_series.append({"t": t_ms, "v": float(r["disk_available_gb"])})
                    if r["memory_used_pct"] is not None:
                        memory_series.append({"t": t_ms, "v": float(r["memory_used_pct"])})
                    if r["load_1m"] is not None:
                        load_series.append({"t": t_ms, "v": float(r["load_1m"])})

                payload["system_series"]["disk_available_gb"] = _downsample(
                    disk_series, MAX_POINTS
                )
                payload["system_series"]["memory_used_pct"] = _downsample(
                    memory_series, MAX_POINTS
                )
                payload["system_series"]["load_1m"] = _downsample(load_series, MAX_POINTS)

                if latest is not None:
                    payload["summary"]["disk_free_gb"] = (
                        float(latest["disk_available_gb"])
                        if latest["disk_available_gb"] is not None
                        else None
                    )
                    payload["summary"]["memory_used_mb"] = (
                        int(latest["memory_used_mb"])
                        if latest["memory_used_mb"] is not None
                        else None
                    )
                    payload["summary"]["latest_system_at"] = latest["recorded_at"].isoformat()
                    cur.execute(
                        """
                        SELECT memory_total_mb
                        FROM sensos.system_stats
                        WHERE memory_total_mb IS NOT NULL
                        ORDER BY recorded_at DESC
                        LIMIT 1
                        """
                    )
                    mem_row = cur.fetchone()
                    if mem_row and mem_row["memory_total_mb"] is not None:
                        payload["summary"]["memory_total_mb"] = int(
                            mem_row["memory_total_mb"]
                        )
                    if latest["load_1m"] is not None:
                        payload["summary"]["load_1m"] = float(latest["load_1m"])

            if has_i2c:
                cur.execute(
                    """
                    SELECT timestamp, key, value
                    FROM sensos.i2c_readings
                    WHERE timestamp >= NOW() - make_interval(hours => %s)
                      AND key = ANY(%s)
                    ORDER BY timestamp ASC
                    """,
                    (WINDOW_HOURS, I2C_KEYS),
                )
                by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                for r in cur.fetchall():
                    ts = r["timestamp"]
                    key = r["key"]
                    if ts is None or key is None or r["value"] is None:
                        continue
                    by_key[key].append({"t": _to_ms(ts), "v": float(r["value"])})

                payload["environment_series"] = {
                    key: _downsample(points, MAX_POINTS)
                    for key, points in by_key.items()
                    if points
                }

    return payload


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/api/dashboard")
def api_dashboard():
    try:
        return jsonify(_fetch_dashboard_payload())
    except Exception as e:
        logger.error("Dashboard API failure: %r", e)
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    payload = {"error": None}
    try:
        payload = _fetch_dashboard_payload()
    except Exception as e:
        logger.error("Initial dashboard render failed: %r", e)
        payload = {"error": str(e)}
    return render_template(
        "index.html",
        initial_payload=payload,
        refresh_seconds=REFRESH_SEC,
    )


if __name__ == "__main__":
    bind = os.environ.get("DASHBOARD_BIND", "0.0.0.0")
    port = env_int("DASHBOARD_INTERNAL_PORT", 8090)
    app.run(host=bind, port=port)
