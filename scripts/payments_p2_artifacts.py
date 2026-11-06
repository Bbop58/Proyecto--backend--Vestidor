"""Fingerprint P2 sources and QA outputs; never read environment files."""
import json
import subprocess
from pathlib import Path
from saas_s6_artifact_manifest import ROOT,sha,fingerprint

def main():
    report={'scope':'P1/P2 local only; not published or installed','repositories':{}}
    for name,dirs,extensions in [
        ('backend',{'app','migrations','scripts','tests'},{'.py','.sql','.txt','.toml'}),
        ('web',{'src','public','e2e'},{'.ts','.js','.mjs','.json','.html','.scss','.css'}),
        ('mobile',{'lib','test','integration_test','android'},{'.dart','.kt','.kts','.xml','.yaml'})]:
        root=ROOT/name
        names=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root).decode().split('\0')
        paths={root/v for v in names if v and not any(p.startswith('.env') for p in Path(v).parts)
               and (Path(v).parts[0] in dirs or Path(v).parent==Path('.')) and Path(v).suffix in extensions}
        for lock in ('pubspec.lock','package-lock.json','Dockerfile','.dockerignore'):
            if (root/lock).is_file():paths.add(root/lock)
        report['repositories'][name]={'base_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),
            'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=root)),'sources':fingerprint(paths,root)}
    bundle=ROOT/'web/dist/web-app/browser'
    report['web_bundle']=fingerprint(bundle.rglob('*'),bundle)
    apk=ROOT/'mobile/build/app/outputs/flutter-apk/app-release.apk'
    report['apk']={'path':apk.relative_to(ROOT).as_posix(),'sha256':sha(apk),'bytes':apk.stat().st_size,
        'api_base':'http://10.0.2.2:58022','api_path':'/api/v1','installed':False,'scope':'Android emulator QA only; not Railway'}
    (ROOT/'docs/auditoria_software/PAGOS_P2_ARTEFACTOS.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('P2 artifact manifest written; no credentials included.')

if __name__=='__main__':main()
