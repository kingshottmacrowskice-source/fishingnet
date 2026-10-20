"""An open PostgreSQL socket is not sufficient during crash recovery."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def load_init():
    spec = importlib.util.spec_from_file_location('init_local_test', Path('infra/init-local.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_open_socket_waits_until_postgres_accepts_queries(monkeypatch):
    module = load_init()
    states = iter([1, 0])
    calls = []
    monkeypatch.setattr(module, 'tcp_ready', lambda: True)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    def probe(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=next(states))
    monkeypatch.setattr(module.subprocess, 'run', probe)
    assert module.wait_ready(timeout=1)
    assert len(calls) == 2
    assert 'pg_isready.exe' in calls[0][0]


def test_recovery_timeout_does_not_report_ready(monkeypatch):
    module = load_init()
    clock = iter([0, 0, 2])
    monkeypatch.setattr(module.time, 'time', lambda: next(clock))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module, 'tcp_ready', lambda: True)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    assert not module.wait_ready(timeout=1)
