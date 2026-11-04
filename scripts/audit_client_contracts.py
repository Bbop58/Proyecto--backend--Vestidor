"""Read-only route/verb audit against the OpenAPI snapshot produced by HTTP tests."""
import json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
snapshot=json.loads((ROOT/'docs/auditoria_software/FASE_5_OPENAPI.json').read_text(encoding='utf-8'))
api=snapshot['operations']
config=(ROOT/'mobile/lib/config/api_config.dart').read_text(encoding='utf-8')
getters=dict(re.findall(r"static String get (\w+) => '\$apiUrl([^']*)'",config))
getters.update(dict(re.findall(r"static String (\w+)\([^)]*\) => '\$apiUrl([^']*)'",config)))
rows=[];unresolved=[]
for channel,folder,glob in [('web','web/src/app','*.ts'),('mobile','mobile/lib','*.dart')]:
 for file in (ROOT/folder).rglob(glob):
  if file.name.endswith('.spec.ts'):continue
  source=file.read_text(encoding='utf-8')
  # Quotes inside template expressions are not delimiters of the enclosing URL.
  source=re.sub(r'\$\{[^}]*\}',lambda m:m[0].replace(chr(39),'_').replace(chr(34),'_'),source)
  bases=dict(re.findall(r"(\w+)\s*=\s*[`']\$\{environment\.apiUrl\}([^`']*)[`']",source))
  pattern=r"(?:this\.http|_http|_api|_apiService|api)\.(getAuth|postAuth|patchAuth|putAuth|deleteAuth|get|post|put|patch|delete)(?:<[^;\n]*?>)?\(\s*([`'\"])(.*?)\2"
  for match in re.finditer(pattern,source,re.S):
   method=match[1].replace('Auth','').upper();raw=match[3]
   url=raw.replace('${environment.apiUrl}','/api/v1').replace('${ApiConfig.apiUrl}','/api/v1')
   for name,base in bases.items():url=url.replace('${this.'+name+'}','/api/v1'+base)
   url=re.sub(r'\$\{[^}]+\}','{id}',url)
   url=re.sub(r'\$[a-zA-Z_][a-zA-Z0-9_]*','{id}',url).split('?')[0]
   if not url.startswith('/api/v1/'):
    unresolved.append({'channel':channel,'file':str(file.relative_to(ROOT)),'expression':raw[:140]});continue
   normalized=re.sub(r'\{[^}]+\}','{id}',url)
   candidates=[item for item in api if re.sub(r'\{[^}]+\}','{id}',item['path'])==normalized]
   rows.append({'channel':channel,'file':str(file.relative_to(ROOT)),'method':method,'path':url,
    'matches':any(item['method']==method for item in candidates)})
  # Simple member URL calls and ApiConfig getter calls, not captured as literals above.
  for match in re.finditer(r"(?:this\.http|_api|_apiService)\.(getAuth|postAuth|patchAuth|putAuth|deleteAuth|get|post|put|patch|delete)(?:<[^;\n]*?>)?\(\s*(?:this\.(\w+)|ApiConfig\.(\w+))",source):
   base=bases.get(match[2]) if match[2] else getters.get(match[3]);method=match[1].replace('Auth','').upper()
   if base is None:continue
   url='/api/v1'+base;normalized=re.sub(r'\$\w+','{id}',url)
   rows.append({'channel':channel,'file':str(file.relative_to(ROOT)),'method':method,'path':url,
    'matches':any(item['method']==method and re.sub(r'\{[^}]+\}','{id}',item['path'])==normalized for item in api)})
result={'api_operations':len(api),'checked_calls':len(rows),'mismatches':[r for r in rows if not r['matches']],
 'calls':rows,'dynamic_expressions_for_review':unresolved}
(ROOT/'docs/auditoria_software/FASE_5_AUDITORIA_CONTRATOS.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({key:result[key] for key in ('api_operations','checked_calls','mismatches')}))
