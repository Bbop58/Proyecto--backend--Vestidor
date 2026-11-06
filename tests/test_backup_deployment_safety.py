import json
from pathlib import Path


def test_backup_worker_refuses_demo_reset_before_opening_database(monkeypatch, capsys):
    from scripts import reset_demo_database as reset
    monkeypatch.setenv('BACKUP_WORKER_ONLY', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://invalid@production.invalid/do-not-touch')
    def forbidden(*args, **kwargs):
        raise AssertionError('A backup worker must never connect to reset a DB')
    monkeypatch.setattr(reset, 'create_engine', forbidden)
    assert reset.main(['--confirm-disposable-demo']) == 2
    assert 'worker' in capsys.readouterr().err.lower()


def test_worker_uses_separate_finite_cron_config_without_reset_or_http_server():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'railway.backups.json').read_text())
    deploy = config['deploy']
    assert deploy['cronSchedule'] == '*/5 * * * *'
    assert deploy['startCommand'] == 'python scripts/run_automatic_backups.py'
    assert deploy['preDeployCommand'] == []
    assert deploy['restartPolicyType'] == 'NEVER'
    assert deploy['healthcheckPath'] is None
    assert config['build']['builder'] == 'DOCKERFILE'
    assert 'reset_demo_database' not in json.dumps(config)
