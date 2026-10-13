"""Verify a clean source checkout without network, login or paid AI calls."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    if (ROOT / "config" / "sources.json").exists() or (ROOT / "config" / "secrets").exists():
        print("Run this check in a clean checkout, not the installed personal application.", file=sys.stderr)
        return 1
    from fastapi.testclient import TestClient
    import webapp
    from src import __version__

    with (patch.object(webapp, "_scheduler_status", return_value={"available": False, "state": "not_installed"}),
          patch("src.task_worker.ensure_worker", side_effect=AssertionError("Empty startup must not start an analysis worker")),
          TestClient(webapp.app) as client):
        for endpoint in ("/", "/static/app.js", "/static/styles.css", "/static/mobile.css", "/static/settings.css", "/api/dashboard", "/api/sources", "/api/settings", "/api/knowledge", "/api/tasks"):
            response = client.get(endpoint, headers={"X-Cyber-Request": "1"})
            if response.status_code != 200:
                raise RuntimeError(f"Clean startup failed at {endpoint}: HTTP {response.status_code}")
            print("Clean startup OK:", endpoint)
        dashboard = client.get("/api/dashboard", headers={"X-Cyber-Request": "1"}).json()
        assert dashboard["source_count"] == 0 and dashboard["unread_count"] == 0
        assert client.get("/api/sources", headers={"X-Cyber-Request": "1"}).json()["sources"] == []
        assert client.get("/api/knowledge", headers={"X-Cyber-Request": "1"}).json()["entries"] == []
        assert client.get("/api/sources").status_code == 403
        assert webapp.app.version == __version__
    for path in (ROOT / "config").glob("*.example.json"):
        json.loads(path.read_text(encoding="utf-8"))
    result = subprocess.run([sys.executable, str(ROOT / "app.py")], input="4\n", text=True, capture_output=True, cwd=ROOT)
    assert result.returncode == 0, "Command-line startup failed."
    assert not (ROOT / "config" / "sources.json").exists(), "Viewing pages unexpectedly created personal settings."
    print("Empty configuration, version, API access protection and CLI startup passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
