import pytest

from apps.usage.receiver import DEFAULT_MAX_WORKERS, max_workers


def test_max_workers_defaults_to_capacity_for_all_gateway_streams(monkeypatch):
    monkeypatch.delenv('GDK_ALS_MAX_WORKERS', raising=False)

    assert max_workers() == DEFAULT_MAX_WORKERS == 256


def test_max_workers_can_be_configured(monkeypatch):
    monkeypatch.setenv('GDK_ALS_MAX_WORKERS', '384')

    assert max_workers() == 384


@pytest.mark.parametrize('value', ['0', '-1', 'many'])
def test_max_workers_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv('GDK_ALS_MAX_WORKERS', value)

    with pytest.raises(ValueError, match='positive integer'):
        max_workers()
