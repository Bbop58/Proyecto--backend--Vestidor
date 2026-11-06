"""P3 source/bundle evidence; P2 mobile binary is referenced, never relabeled."""
import json,subprocess
from pathlib import Path
from saas_s6_artifact_manifest import ROOT,sha,fingerprint

def main():
    report={'scope':'P1-P3 local only; not deployed; provider acceptance pending P4','repositories':{}}
    for name,dirs,extensions in [
        ('backend',{'app','migrations','scripts','tests'},{'.py','.sql','.txt','.toml'}),
        ('web',{'src','public','e2e'},{'.ts','.js','.mjs','.json','.html','.scss','.css'}),
        ('mobile',{'lib','test','integration_test','android'},{'.dart','.kt','.kts','.xml','.yaml'})]:
        root=ROOT/name
        names=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root).decode().split('\0')
        paths={root/v for v in names if v and not any(part.startswith('.env') for part in Path(v).parts)
            and (Path(v).parts[0] in dirs or Path(v).parent==Path('.')) and Path(v).suffix in extensions}
        for lock in ('pubspec.lock','package-lock.json','Dockerfile','.dockerignore'):
            if (root/lock).is_file():paths.add(root/lock)
        report['repositories'][name]={'base_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),
            'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=root)),'sources':fingerprint(paths,root)}
    bundle=ROOT/'web/dist/web-app/browser';report['web_bundle']=fingerprint(bundle.rglob('*'),bundle)
    previous=json.loads((ROOT/'docs/auditoria_software/PAGOS_P2_ARTEFACTOS.json').read_text(encoding='utf-8'))
    apk=ROOT/'mobile/build/app/outputs/flutter-apk/app-release.apk'
    report['mobile_binary']={'scope':'P2 emulator QA binary, not a new P3/Railway APK',
        'sha256':sha(apk),'unchanged_since_p2':sha(apk)==previous['apk']['sha256'],
        'mobile_sources_unchanged':report['repositories']['mobile']['sources']['sha256']==previous['repositories']['mobile']['sources']['sha256'],
        'installed':False,'api_base':previous['apk']['api_base']}
    (ROOT/'docs/auditoria_software/PAGOS_P3_ARTEFACTOS.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('P3 source and bundle manifest written; no environment files read.')

if __name__=='__main__':main()
