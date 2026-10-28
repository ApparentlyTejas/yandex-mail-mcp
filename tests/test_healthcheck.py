from yandex_mail_mcp.healthcheck import is_healthy
from yandex_mail_mcp.monitor import open_state


def test_healthcheck_requires_recent_successful_poll(tmp_path):
    path = str(tmp_path / "state.sqlite")
    assert not is_healthy(path, 180, now=5000)
    conn = open_state(path)
    assert not is_healthy(path, 180, now=5000)
    conn.execute("INSERT INTO monitor_heartbeat (id, updated_at) VALUES (1, 4000)")
    conn.commit()
    assert is_healthy(path, 180, now=5000)
    assert not is_healthy(path, 180, now=8000)
    conn.close()
