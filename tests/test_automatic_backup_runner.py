import subprocess
from types import SimpleNamespace
from scripts import run_automatic_backups as runner


def test_runner_enforces_process_deadline_and_propagates_status(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(runner.subprocess, 'run', run)
    assert runner.main([]) == 7
    assert calls[0][0][-1] == '--cycle'
    assert calls[0][1]['timeout'] == 240


def test_runner_timeout_is_failure_without_dumping_provider_data(monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('private-synthetic-command', 240)
    monkeypatch.setattr(runner.subprocess, 'run', timeout)
    assert runner.main([]) == 1
    assert 'private-synthetic' not in capsys.readouterr().err


def test_runner_refuses_unexpected_arguments():
    assert runner.main(['--reset']) == 2
