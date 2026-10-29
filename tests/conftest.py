import dataclasses
import socket
import threading

import pytest

import config


@pytest.fixture
def test_settings(tmp_path, monkeypatch):
    """Point every module that imported ``settings`` at a temporary database and files."""
    replaced = dataclasses.replace(
        config.settings,
        database_path=tmp_path / "velo.db",
        gmail_cursor_file=tmp_path / "cursor",
        agent_screenshot_dir=tmp_path / "shots",
        agent_max_steps=8,
    )
    import agent.sandbox
    import agent.vision_agent
    import scripts.cancel_subscription
    import scripts.run_pipeline_once
    for module in (config, agent.sandbox, agent.vision_agent, scripts.run_pipeline_once, scripts.cancel_subscription):
        monkeypatch.setattr(module, "settings", replaced)
    return replaced


@pytest.fixture
def mock_site_url():
    """Serve the sandbox site on a free loopback port for the duration of a test."""
    from werkzeug.serving import make_server
    from agent.mock_site.app import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = make_server("127.0.0.1", port, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
