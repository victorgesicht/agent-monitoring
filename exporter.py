#!/usr/bin/env python3
"""opencode usage exporter — emits Prometheus metrics from the local opencode SQLite DB.

Metrics exposed:
  opencode_sessions_total{agent,model}          cumulative number of sessions
  opencode_sessions{tool}                       active (non-archived) session count by tool
  opencode_tokens_input_total{agent}               cumulative input tokens
  opencode_tokens_output_total{agent}              cumulative output tokens
  opencode_tokens_reasoning_total{agent}           cumulative reasoning tokens
  opencode_tokens_cache_read_total{agent}          cumulative cache-read tokens
  opencode_tokens_cache_write_total{agent}         cumulative cache-write tokens
  opencode_cost_total{agent}                       cumulative cost (USD)
  opencode_usage_per_day{agent,metric}             cumulative token/cost per calendar day (UTC)
  opencode_messages_total{agent}                   cumulative message count
  opencode_latest_parts_per_minute{agent}          rate helper: parts created in last 60s
  opencode_project_sessions_total{project}         cumulative sessions per project
  opencode_db_page_count / opencode_db_size_bytes  db size gauges
Run: python3 exporter.py [port]  (default 9105)
"""
import http.server
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9105
WAL_BUSY_MS = 3000


class UsageCollector:
    def collect(self):
        lines = []
        fresh = int(time.time())  # noqa
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro&immutable=1", uri=True, timeout=2)
        except Exception:
            return lines
        con.execute(f"PRAGMA busy_timeout={WAL_BUSY_MS}")
        try:
            cur = con.cursor()
            # overall aggregated by agent
            cur.execute(
                "SELECT coalesce(agent,'unknown') FROM session WHERE parent_id IS NULL "
                "GROUP BY agent ORDER BY agent"
            )
            agents = [r[0] for r in cur.fetchall() or []]

            def q(label):
                rows = con.execute(
                    f"SELECT coalesce(agent,'unknown'), {label} FROM session WHERE parent_id IS NULL GROUP BY agent"
                ).fetchall()
                return {a: (v or 0) for a, v in rows}

            per = {
                "tokens_input": q("SUM(tokens_input)"),
                "tokens_output": q("SUM(tokens_output)"),
                "tokens_reasoning": q("SUM(tokens_reasoning)"),
                "tokens_cache_read": q("SUM(tokens_cache_read)"),
                "tokens_cache_write": q("SUM(tokens_cache_write)"),
                "cost": q("SUM(cost)"),
            }
            sessions_total = con.execute(
                "SELECT coalesce(agent,'unknown'), COUNT(*) FROM session WHERE parent_id IS NULL GROUP BY agent"
            ).fetchall()
            messages_total = con.execute(
                "SELECT coalesce(s.agent,'unknown'), COUNT(m.id) FROM message m "
                "JOIN session s ON s.id=m.session_id GROUP BY s.agent"
            ).fetchall()
            tools_placeholder = None
            # sessions (all rows incl children) by tool for gauges
            sess_tool = con.execute(
                "SELECT coalesce(agent,'unknown'), COUNT(*) FROM session GROUP BY agent"
            ).fetchall()
            # parts in last 60s
            now_ms = int(time.time() * 1000)
            parts_60 = con.execute(
                "SELECT count(*) FROM part WHERE time_created > ?", (now_ms - 60_000,)
            ).fetchone()[0]
            # per-day history (UTC) of totals for each agent+metric
            days = con.execute(
                """
                SELECT coalesce(agent,'unknown'),
                       date(time_created/1000, 'unixepoch') AS d,
                       SUM(tokens_input), SUM(tokens_output),
                       SUM(tokens_reasoning), SUM(tokens_cache_read),
                       SUM(tokens_cache_write), SUM(cost)
                FROM session WHERE parent_id IS NULL
                GROUP BY agent, d ORDER BY d
                """
            ).fetchall()
            # per project sessions (directory basename)
            proj = con.execute(
                "SELECT "
                "CASE WHEN directory IS NULL THEN 'unknown' ELSE "
                "replace(directory, char(92), '/') END, COUNT(*) "
                "FROM session WHERE parent_id IS NULL GROUP BY directory"
            ).fetchall()
            db_size = os.path.getsize(DB) if os.path.exists(DB) else 0
            page_count = con.execute("PRAGMA page_count").fetchone()[0]
        finally:
            con.close()

        lines.append(f"# HELP opencode_sessions_total Cumulative opencode sessions. (unused scaffold)")
        for a, c in sessions_total:
            lines.append(f'opencode_sessions_total{{agent="{a}"}} {c}')
        for a, c in sess_tool:
            lines.append(f'opencode_sessions{{agent="{a}"}} {c}')
        for metric, mapp in per.items():
            for a, v in mapp.items():
                v = v if v is not None else 0
                if metric == "cost":
                    lines.append(f'# HELP opencode_cost_total Cumulative spend USD.')
                    lines.append(f'opencode_cost_total{{agent="{a}"}} {v:.6f}')
                else:
                    lines.append(f'opencode_{metric}_total{{agent="{a}"}} {int(v)}')
        for metric, idx in (("tokens_input", 2), ("tokens_output", 3), ("tokens_reasoning", 4),
                            ("tokens_cache_read", 5), ("tokens_cache_write", 6)):
            for a, d, *vals in days:
                v = vals[idx - 2] or 0
                lines.append(f'opencode_usage_per_day{{agent="{a}",metric="{metric}",day="{d}"}} {int(v)}')
        for a, d, *vals in days:
            lines.append(f'opencode_cost_per_day{{agent="{a}",day="{d}"}} {(vals[5] or 0):.6f}')
        for a, c in messages_total:
            lines.append(f'opencode_messages_total{{agent="{a}"}} {c}')
        lines.append(f'opencode_latest_parts_per_minute {{agent="all"}} {int(parts_60)}')
        for p, c in proj:
            lines.append(f'opencode_project_sessions_total{{project="{p.rstrip(chr(47)).split(chr(47))[-1]}"}} {c}')
        lines.append(f'opencode_db_size_bytes {int(db_size)}')
        lines.append(f'opencode_db_page_count {int(page_count)}')
        lines.append(f'opencode_up 1')
        return lines


collector = UsageCollector()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/metrics"):
            body = "\n".join(collector.collect()) + "\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()