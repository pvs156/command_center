import pytest

from app import career


@pytest.fixture(autouse=True)
def isolated_career(tmp_path, monkeypatch):
    """Tests never read or write your real data/career profile."""
    monkeypatch.setattr(career, 'DIR', tmp_path / 'career')
    monkeypatch.setattr(career, 'MD', tmp_path / 'career' / 'career.md')
    monkeypatch.setattr(career, 'JSON', tmp_path / 'career' / 'career.json')
