import json
import urllib.request

urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

host = "127.0.0.1:9999"
exprs = {
    "sessions": 'opencode_sessions{job="opencode"}',
    "messages": 'opencode_messages_total{job="opencode"}',
    "parts/min": 'opencode_latest_parts_per_minute{job="opencode"}',
    "in/day": 'max by (day) (opencode_usage_per_day{metric="tokens_input",job="opencode"})',
    "out/day": 'max by (day) (opencode_usage_per_day{metric="tokens_output",job="opencode"})',
    "cache/day": 'max by (day) (opencode_usage_per_day{metric="tokens_cache_read",job="opencode"})',
    "projects": 'opencode_project_sessions_total{job="opencode"}',
    "db_size": 'opencode_db_size_bytes{job="opencode"}',
    "cost": 'opencode_cost_total{job="opencode"}',
}

for name, expr in exprs.items():
    body = json.dumps({
        "from": "now-24h", "to": "now",
        "queries": [{"refId": "A", "datasource": {"type": "prometheus", "uid": "prometheus"},
                     "expr": expr, "instant": True}],
    }).encode()
    req = urllib.request.Request("http://%s/api/ds/query" % host, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req))
        r = d["results"]["A"]
        shown = []
        for f in r.get("frames", []):
            for c in f["schema"]["fields"]:
                if c["name"] != "Time":
                    vals = f["schema"].get("data", {}).get("values", [[], []])
                    shown.append((c["name"], vals[0][:4] if vals[0] else []))
        print(f"{name:12s} OK  {shown}")
    except Exception as e:
        print(f"{name:12s} FAIL {e}")