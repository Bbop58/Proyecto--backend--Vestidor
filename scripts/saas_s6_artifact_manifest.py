"""Fingerprint local SaaS sources and test artifacts without reading environment files."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(paths, root):
    rows = [{'path': p.relative_to(root).as_posix(), 'sha256': sha(p)}
            for p in sorted(paths) if p.is_file()]
    return {'files': len(rows), 'sha256': hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'entries': rows}


def main():
    report = {'scope': 'local uncommitted SaaS; not deployed or installed', 'repositories': {}}
    for name, directories, extensions in [
        ('backend', {'app', 'migrations', 'scripts', 'tests'}, {'.py', '.sql', '.txt', '.toml'}),
        ('web', {'src', 'public', 'e2e'}, {'.ts', '.js', '.mjs', '.json', '.html', '.scss', '.css'}),
        ('mobile', {'lib', 'test', 'integration_test', 'android'}, {'.dart', '.kt', '.kts', '.xml', '.yaml'}),
    ]:
        root = ROOT / name
        names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')
        paths = {root / value for value in names if value
                 and not any(part.startswith('.env') for part in Path(value).parts)
                 and (Path(value).parts[0] in directories or Path(value).parent == Path('.'))
                 and Path(value).suffix in extensions}
        # Dependency lockfiles participate even when their suffix is .lock.
        for lock in ('pubspec.lock', 'package-lock.json', 'Dockerfile', '.dockerignore'):
            if (root / lock).is_file(): paths.add(root / lock)
        report['repositories'][name] = {
            'base_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
            'dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root)),
            'sources': fingerprint(paths, root),
        }
    bundle = ROOT / 'web/dist/web-app/browser'
    if not bundle.is_dir(): raise RuntimeError('Build the web bundle first')
    report['web_bundle'] = fingerprint(bundle.rglob('*'), bundle)
    apk = ROOT / 'mobile/build/app/outputs/flutter-apk/app-release.apk'
    report['apk'] = {'path': apk.relative_to(ROOT).as_posix(), 'sha256': sha(apk),
                     'bytes': apk.stat().st_size, 'api': 'http://10.0.2.2:58016',
                     'signing': 'existing project debug signing; local QA only', 'installed': False}
    output = ROOT / 'docs/auditoria_software/SAAS_S6_ARTEFACTOS.json'
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'manifest': output.name, 'apk_sha256': report['apk']['sha256'],
                      'web_bundle_sha256': report['web_bundle']['sha256']}))


if __name__ == '__main__': main()
