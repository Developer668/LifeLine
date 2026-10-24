"""Every test receives its own database, including the inherited baseline suite."""
import pytest

@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv('RELAY_DB', str(tmp_path / 'test.db'))
    from relay import store
    from relay.coordination import initialize as contacts
    from relay.media import initialize as media
    store.initialize()
    contacts()
    media()
