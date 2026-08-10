#!/usr/bin/env python3
"""Probe the 7 Standard Report dashboards and print all query names + fields."""

import json
import os
import sys
import urllib.request

BASE_URL = "https://moxie.omniapp.co/api"

DASHBOARDS = {
    "b7913ead": "???",
    "506ada68": "???",
    "eab2b375": "???",
    "d6776514": "???",
    "b8baa4c2": "???",
    "fed9785d": "???",
    "76abf294": "???",
}


def api_get(path: str, api_key: str):
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def probe(api_key: str):
    for did, label in DASHBOARDS.items():
        print(f"\n{'='*60}")
        print(f"Dashboard: {did}  ({label})")
        print(f"{'='*60}")
        try:
            data = api_get(f"/v1/documents/{did}/queries", api_key)
            queries = data if isinstance(data, list) else data.get("queries", data.get("data", []))
            if not isinstance(queries, list):
                print(f"  Unexpected response shape: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                print(f"  Raw: {json.dumps(data)[:400]}")
                continue
            print(f"  {len(queries)} queries found:")
            for q in queries:
                name = q.get("name", "(unnamed)")
                fields = q.get("fields", [])
                filters = q.get("filters", [])
                print(f"\n  [{name}]")
                print(f"    fields:  {fields}")
                if filters:
                    print(f"    filters: {[f.get('field','?') for f in filters]}")
        except Exception as e:
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    key = os.environ.get("OMNI_API_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not key:
        print("Usage: OMNI_API_KEY=your_key python scripts/probe_standard_reports.py")
        print("   or: python scripts/probe_standard_reports.py your_key")
        sys.exit(1)
    probe(key)
