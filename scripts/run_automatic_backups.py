"""One finite backup cycle. No seed, migration, reset or web server startup."""
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_cycle():
    from app.database import engine
    from app.services.automatic_backup_service import run_due_backups
    try:
        print(json.dumps(run_due_backups(engine)))
        return 0
    except Exception as error:
        # No credentials, provider bodies or connection URL in output.
        print('Automatic backups failed: ' + type(error).__name__, file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['--cycle']:
        return run_cycle()
    if argv:
        print('Unexpected backup runner arguments', file=sys.stderr)
        return 2
    # Hard deadline also covers a provider that keeps a socket open indefinitely.
    # Killing this dedicated child releases PostgreSQL session locks; its next
    # cycle reconciles RUNNING records and any already-uploaded object.
    try:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), '--cycle'],
            timeout=240, check=False).returncode
    except subprocess.TimeoutExpired:
        print('Automatic backup cycle exceeded 240 seconds; retry next cycle', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
