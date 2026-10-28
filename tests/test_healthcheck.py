import pytest

from yandex_mail_mcp.healthcheck import is_healthy
from yandex_mail_mcp.monitor import open_state, save_heartbeat


def test_healthcheck_requires_recent_successful_poll(tmp_path):
    path = str(tmp_path / "state.sqlite")
    assert not is_healthy(path, 180, now=5000)
    conn = open_state(path)
    assert not is_healthy(path, 180, now=5000)
    conn.execute("INSERT INTO monitor_heartbeat (id, updated_at) VALUES (1, 4000)")
    conn.commit()
    assert is_healthy(path, 180, now=5000)
    assert not is_healthy(path, 180, now=8000)
    assert is_healthy(path, 180, now=3999)
    conn.close()


def test_record_heartbeat_uses_python_timestamp(tmp_path, monkeypatch):
    path = str(tmp_path / "state.sqlite")
    conn = open_state(path)
    monkeypatch.setattr("yandex_mail_mcp.monitor.time.time", lambda: 1234.9)
    save_heartbeat(conn)
    assert conn.execute("SELECT updated_at FROM monitor_heartbeat").fetchone() == (1234,)
    assert is_healthy(path, 180, now=1235)
    conn.close()


def test_healthcheck_main_loads_dotenv(monkeypatch, tmp_path):
    from yandex_mail_mcp import healthcheck

    path = str(tmp_path / "state.sqlite")
    conn = open_state(path)
    save_heartbeat(conn)
    monkeypatch.delenv("STATE_DB", raising=False)
    monkeypatch.setattr(healthcheck, "load_dotenv", lambda: monkeypatch.setenv("STATE_DB", path))
    with pytest.raises(SystemExit) as exc:
        healthcheck.main()
    assert exc.value.code == 0
    conn.close()
