"""Real HTTP/process regression; uses only a temporary DB and no credentials."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Windows terminate is not POSIX SIGTERM")
def test_http_weight_and_graceful_process_restart(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "RUN_BOT": "false", "DEV_MODE": "true", "BOT_TOKEN": "",
           "OPENAI_API_KEY": "", "OWNER_TELEGRAM_ID": "0", "HOST": "0.0.0.0",
           "PORT": str(port), "DATABASE_PATH": str(tmp_path / "runtime.db")}
    for iteration in range(2):
        proc = subprocess.Popen([sys.executable, "run.py"], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
                for _ in range(80):
                    assert proc.poll() is None, "Server exited during startup"
                    try:
                        health = client.get("/health")
                        if health.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Server did not become healthy")
                assert health.json()["version"] == (root / "VERSION").read_text().strip()
                assert health.json()["port"] == port
                assert client.get("/").status_code == 200
                probe = subprocess.run([sys.executable, "scripts/healthcheck.py"], cwd=root,
                                       env=env, capture_output=True, text=True, timeout=5)
                assert probe.returncode == 0, probe.stdout + probe.stderr
                if iteration == 0:
                    profile = {"telegram_user_id": 1, "name": "Runtime test", "sex": "male",
                               "age": 25, "height_cm": 180, "weight_kg": 70, "target_weight_kg": 78}
                    client.post("/api/profile", json=profile).raise_for_status()
                    client.post("/api/weight", json={"telegram_user_id": 1, "weight_kg": 71.2}).raise_for_status()
                else:
                    assert client.get("/api/progress").json()["weights"][-1]["weight_kg"] == 71.2
                # Allow the startup self-probe to run after Uvicorn opens the listener.
                time.sleep(0.7)
        finally:
            proc.terminate()
            try:
                output = proc.communicate(timeout=8)[0]
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                pytest.fail("SIGTERM shutdown timed out")
            assert "Application shutdown complete" in output, output
            assert "LOCAL_HTTP_OK" in output, output
