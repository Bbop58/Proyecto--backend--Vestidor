"""Genera un modelo StarUML y rutas ortogonales desde el catálogo DB auditado.

No consulta ni modifica la DB. StarUML construye sus propias vistas nativas mediante
su CLI oficial y una plantilla EJS, para no imitar a mano su serialización interna.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from pathlib import Path
import heapq
import json
import math
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).parent))
from auditar_documentacion_bd import cardinality, unique_sets

OUT=ROOT/'diseno conceptual DB'
GLOBAL=[
 ['token_blacklist','permissions','role_permissions','roles','subscription_plans','subscriptions'],
 ['notifications','audit_logs','users','tenants','sucursales','tarifas_delivery'],
 ['favoritos','categorias','productos','variantes_producto','inventario','movimientos_inventario'],
 ['proveedores','temporadas','detalles_venta','ventas','reservas','detalles_reserva'],
 [None,None,'sync_operations','cambios_producto','detalles_cambio_producto',None],
]
MODULES=[
 ('01 Empresas y suscripciones',[['subscription_plans','subscriptions','tenants'],['users','sucursales',None]]),
 ('02 Usuarios roles y permisos',[['permissions','role_permissions','roles'],['token_blacklist','users',None]]),
 ('03 Catalogo variantes y favoritos',[['tenants','categorias','proveedores'],['users','productos','temporadas'],['favoritos','variantes_producto',None]]),
 ('04 Inventario y movimientos',[['tenants','users','sucursales'],['productos','variantes_producto','inventario'],[None,'movimientos_inventario',None]]),
 ('05 Ventas y reservas',[['users','sucursales','variantes_producto'],['ventas','reservas','inventario'],['detalles_venta','detalles_reserva',None]]),
 ('06 Cambios de prendas',[['users','ventas','sucursales'],['variantes_producto','cambios_producto','detalles_cambio_producto']]),
 ('07 Envios y tarifas',[['tenants','sucursales'],['ventas','tarifas_delivery']]),
 ('08 Auditoria notificaciones y offline',[['tenants','users','sucursales'],['audit_logs','notifications','sync_operations']]),
]


def reference(identifier):return {'$ref':identifier}
def cid(name):return 'FS_TABLE_'+name
def did(n):return 'FS_DIAGRAM_'+str(n)


def boxes(grid,tables,show_type):
    width=400 if show_type else 280
    col_step=width+200
    y=100
    result={}
    for row in grid:
        heights=[math.ceil((58+len(tables['public.'+name]['columns'])*17)/20)*20 for name in row if name]
        max_h=max(heights or [180])
        for c,name in enumerate(row):
            if name:
                height=math.ceil((58+len(tables['public.'+name]['columns'])*17)/20)*20
                result[name]=dict(x=100+c*col_step,y=y,w=width,h=height)
        y+=max_h+180
    return result


def route_edges(rects, edges):
    """A* sobre cuadrícula, con puertos separados y penalización de cruces y solapes."""
    step=20;stubs=40
    incident=defaultdict(list)
    def center(r):return r['x']+r['w']/2,r['y']+r['h']/2
    for i,e in enumerate(edges):
        for end,other in [('a','b'),('b','a')]:
            own=rects[e[end]];target=rects[e[other]];x,y=center(own);tx,ty=center(target)
            dx=tx-x;dy=ty-y
            side=('R' if dx>=0 else 'L') if abs(dx)>abs(dy)*1.15 else ('B' if dy>=0 else 'T')
            incident[(e[end],side)].append((i,end,ty if side in 'LR' else tx))
    ports={}
    for (name,side),items in incident.items():
        items.sort(key=lambda x:(x[2],x[0]));r=rects[name]
        length=r['h'] if side in 'LR' else r['w']
        for k,(i,end,_) in enumerate(items):
            off=round(((k+1)*length/(len(items)+1))/step)*step
            off=max(20,min(length-20,off))
            if side=='L':p=(r['x'],r['y']+off);v=(-1,0)
            elif side=='R':p=(r['x']+r['w'],r['y']+off);v=(1,0)
            elif side=='T':p=(r['x']+off,r['y']);v=(0,-1)
            else:p=(r['x']+off,r['y']+r['h']);v=(0,1)
            ports[(i,end)]=(p,(p[0]+stubs*v[0],p[1]+stubs*v[1]))
    maxx=max(r['x']+r['w'] for r in rects.values())+160
    maxy=max(r['y']+r['h'] for r in rects.values())+160
    blocked=set()
    for r in rects.values():
        for x in range(r['x']//step,(r['x']+r['w'])//step+1):
            for y in range(r['y']//step,(r['y']+r['h'])//step+1):blocked.add((x,y))
    used=Counter();directions=defaultdict(set);paths={};stats={'unroutable':0,'overlaps_with_boxes':0}
    dirs=[(1,0),(0,1),(-1,0),(0,-1)]
    order=sorted(range(len(edges)),key=lambda i:sum(abs(ports[(i,'a')][1][j]-ports[(i,'b')][1][j]) for j in [0,1]),reverse=True)
    for i in order:
        pa,sa=ports[(i,'a')];pb,sb=ports[(i,'b')];start=(sa[0]//step,sa[1]//step);goal=(sb[0]//step,sb[1]//step)
        initial=(start[0],start[1],4);distance={initial:0};previous={};queue=[(0,0,initial)];last=None
        while queue:
            _,cost,state=heapq.heappop(queue)
            if cost!=distance[state]:continue
            x,y,old=state
            if (x,y)==goal:last=state;break
            for direction,(dx,dy) in enumerate(dirs):
                nx,ny=x+dx,y+dy
                if nx<1 or ny<1 or nx>maxx//step or ny>maxy//step:continue
                if (nx,ny) in blocked and (nx,ny) not in [start,goal]:continue
                segment=tuple(sorted(((x,y),(nx,ny))))
                penalty=used[segment]*45
                crossing=12 if directions[(nx,ny)] and direction%2 not in directions[(nx,ny)] else 0
                new_cost=cost+10+(14 if old!=4 and old!=direction else 0)+penalty+crossing
                new=(nx,ny,direction)
                if new_cost<distance.get(new,10**20):
                    distance[new]=new_cost;previous[new]=state
                    estimate=(abs(nx-goal[0])+abs(ny-goal[1]))*10
                    heapq.heappush(queue,(new_cost+estimate,new_cost,new))
        if last is None:raise RuntimeError('No hay ruta para '+str(edges[i]))
        grid=[]
        while last!=initial:grid.append((last[0],last[1]));last=previous[last]
        grid.append(start);grid.reverse()
        for a,b in zip(grid,grid[1:]):
            used[tuple(sorted((a,b)))]+=1
            directions[a].add(0 if a[0]!=b[0] else 1);directions[b].add(0 if a[0]!=b[0] else 1)
        raw=[pa]+[(x*step,y*step) for x,y in grid]+[pb]
        compact=[]
        for p in raw:
            if compact and compact[-1]==p:continue
            if len(compact)>=2 and ((compact[-2][0]==compact[-1][0]==p[0]) or (compact[-2][1]==compact[-1][1]==p[1])):compact[-1]=p
            else:compact.append(p)
        paths[edges[i]['id']]=compact
    stats['shared_grid_segments']=sum(n>1 for n in used.values())
    stats['orthogonal_paths']=len(paths)
    for i,e in enumerate(edges):
        pts=paths[e['id']]
        for a,b in zip(pts,pts[1:]):
            assert a[0]==b[0] or a[1]==b[1]
            for name,r in rects.items():
                # Solo interior estricto: se permiten conexiones sobre el perímetro.
                if a[0]==b[0]:hit=r['x']<a[0]<r['x']+r['w'] and max(min(a[1],b[1]),r['y'])<min(max(a[1],b[1]),r['y']+r['h'])
                else:hit=r['y']<a[1]<r['y']+r['h'] and max(min(a[0],b[0]),r['x'])<min(max(a[0],b[0]),r['x']+r['w'])
                if hit:stats['overlaps_with_boxes']+=1
    assert stats['overlaps_with_boxes']==0,stats
    return paths,stats


def main():
    OUT.mkdir(exist_ok=True)
    snapshot=json.loads((ROOT/'documentacion de base de datos/esquema_verificado.json').read_text(encoding='utf-8'))
    tables=snapshot['database']['tables'];project_id='FS_DATABASE_PROJECT';model_id='FS_DATABASE_MODEL'
    project={'_type':'Project','_id':project_id,'name':'FashionStore Modelo conceptual de base de datos',
             'author':'FashionStore','documentation':'Modelo editable generado desde el catálogo físico verificado '+snapshot['verified_at']+'. 27 tablas de negocio y una técnica; 263 atributos; 50 FK. La vista general no inventa tablas ni relaciones.','ownedElements':[]}
    model={'_type':'UMLModel','_id':model_id,'_parent':reference(project_id),'name':'Base de datos FashionStore','ownedElements':[]}
    project['ownedElements'].append(model)
    enum_ids={}
    for e in snapshot['database']['enums']:
        eid='FS_ENUM_'+e['name'];enum_ids[e['name']]=eid
        model['ownedElements'].append({'_type':'UMLEnumeration','_id':eid,'_parent':reference(model_id),'name':e['name'],
            'documentation':'Tipo ENUM físico public.'+e['name'],'literals':[{'_type':'UMLEnumerationLiteral','_id':eid+'_'+str(i),'_parent':reference(eid),'name':v} for i,v in enumerate(e['labels'])]})
    for key,t in tables.items():
        name=t['name'];identifier=cid(name);pk=set(t['pk']['constrained_columns']);single={next(iter(s)) for s in unique_sets(t) if len(s)==1};attrs=[]
        for col in t['columns']:
            typ=reference(enum_ids[col['enum_name']]) if col.get('enum_name') else col['type']
            fks=[f for f in t['fks'] if col['name'] in f['constrained_columns']]
            attrs.append({'_type':'UMLAttribute','_id':identifier+'_ATTR_'+col['name'],'_parent':reference(identifier),
                          'name':col['name'],'type':typ,'visibility':'public','multiplicity':'0..1' if col['nullable'] else '',
                          'isID':col['name'] in pk,'isUnique':col['name'] in single and col['name'] not in pk,
                          'documentation':json.dumps({'physical_column':key+'.'+col['name'],'sql_type':col['type'],'nullable':col['nullable'],'primary_key_component':col['name'] in pk,'server_default':col.get('server_default'),'foreign_keys':fks},ensure_ascii=False,indent=2)})
        model['ownedElements'].append({'_type':'UMLClass','_id':identifier,'_parent':reference(model_id),'name':name,'visibility':'public',
            'documentation':json.dumps({'physical_table':key,'source_orm_class':snapshot['orm'].get(name,{}).get('class'),'pk':t['pk'],'unique':t['unique'],'indexes':t['indexes'],'checks':t['checks'],'technical':name=='alembic_version'},ensure_ascii=False,indent=2),
            'attributes':attrs,'operations':[],'ownedElements':[]})
    edges=[]
    pair_counts=Counter((f['referred_table'],t['name']) for t in tables.values() for f in t['fks'])
    for key,t in tables.items():
        for i,f in enumerate(t['fks']):
            identifier='FS_REL_'+t['name']+'_'+str(i);a=f['referred_table'];b=t['name'];parent,child=cardinality(t,f)
            assoc={'_type':'UMLAssociation','_id':identifier,'_parent':reference(model_id),
                   'name':','.join(f['constrained_columns']) if pair_counts[(a,b)]>1 else '',
                   'documentation':json.dumps({'physical_fk':f,'parent_per_child':parent,'children_per_parent':child},ensure_ascii=False,indent=2),
                   'end1':{'_type':'UMLAssociationEnd','_id':identifier+'_PARENT','_parent':reference(identifier),'name':'','reference':reference(cid(a)),'multiplicity':parent,'navigable':'unspecified','aggregation':'none'},
                   'end2':{'_type':'UMLAssociationEnd','_id':identifier+'_CHILD','_parent':reference(identifier),'name':'','reference':reference(cid(b)),'multiplicity':child,'navigable':'unspecified','aggregation':'none'}}
            model['ownedElements'].append(assoc);edges.append({'id':identifier,'a':a,'b':b,'parent':parent,'child':child,'fk':f['name']})
    specs=[]
    for index,(name,grid,show_type) in enumerate([('00 Modelo conceptual completo',GLOBAL,False)]+[(name,grid,True) for name,grid in MODULES]):
        rects=boxes(grid,tables,show_type);chosen=[e for e in edges if e['a'] in rects and e['b'] in rects];paths,stats=route_edges(rects,chosen)
        diagram={'_type':'UMLClassDiagram','_id':did(index),'_parent':reference(model_id),'name':name,'ownedViews':[]}
        model['ownedElements'].append(diagram)
        specs.append({'id':did(index),'name':name,'showType':show_type,'nodes':[dict(id=cid(n),table=n,**r) for n,r in rects.items()],
                      'edges':[dict(e,points=paths[e['id']]) for e in chosen],'routing':stats})
    technical_index=len(specs)
    model['ownedElements'].append({'_type':'UMLClassDiagram','_id':did(technical_index),'_parent':reference(model_id),'name':'09 Tipos enumerados y control de migraciones','ownedViews':[]})
    specs.append({'id':did(technical_index),'name':'09 Tipos enumerados y control de migraciones','showType':True,
                  'nodes':[{'id':cid('alembic_version'),'table':'alembic_version','x':100,'y':100,'w':400,'h':120}]+[
                      {'id':eid,'table':name,'x':100+(i%3)*600,'y':350+(i//3)*350,'w':400,'h':250,'enum':True} for i,(name,eid) in enumerate(enum_ids.items())],
                  'edges':[],'routing':{'orthogonal_paths':0,'overlaps_with_boxes':0}})
    # El primer diagrama es la vista general, sin modificar la propiedad de las entidades.
    diagrams=[e for e in model['ownedElements'] if e['_type']=='UMLClassDiagram']
    rest=[e for e in model['ownedElements'] if e['_type']!='UMLClassDiagram']
    model['ownedElements']=diagrams+rest
    (OUT/'modelo_base.mdj').write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'distribucion.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2),encoding='utf-8')
    target=OUT/'FashionStore_Modelo_Conceptual_DB.mdj'
    script=r'''<%
const fs = require('fs');
const specs = JSON.parse(fs.readFileSync(SPECS_PATH,'utf8'));
const report=[];
for (const spec of specs) {
  const diagram=app.repository.get(spec.id);
  const views={};
  for (const node of spec.nodes) {
    const model=app.repository.get(node.id);
    const view=node.enum ? new app.type.UMLEnumerationView() : new app.type.UMLClassView();
    view._parent=diagram;view.model=model;
    view.left=node.x;view.top=node.y;view.width=node.w;view.height=node.h;
    view.font.face='Arial';view.font.size=12;view.font.style=0;
    view.fillColor='#FFFFFF';view.lineColor='#27313A';view.fontColor='#111827';
    view.showShadow=false;view.showNamespace=false;view.showVisibility=false;
    view.showProperty=true;view.showType=spec.showType;view.showMultiplicity=true;
    view.suppressOperations=true;view.suppressAttributes=false;view.suppressReceptions=true;
    view.autoResize=false;view.wordWrap=false;
    app.engine.addViews(diagram,[view]);
    views[node.table]=view;
  }
  for (const edge of spec.edges) {
    const view=new app.type.UMLAssociationView();
    view._parent=diagram;view.model=app.repository.get(edge.id);
    view.tail=views[edge.a];view.head=views[edge.b];
    view.showShadow=false;view.lineColor='#45505B';view.fontColor='#111827';view.fillColor='#FFFFFF';
    view.font.face='Arial';view.font.size=11;view.font.style=0;
    view.showVisibility=false;view.showProperty=false;view.showMultiplicity=true;view.showType=false;
    view.lineStyle=0;view.points.__read(edge.points.map(p=>p[0]+':'+p[1]).join(';'));
    view.tailMultiplicityLabel.distance=28;view.headMultiplicityLabel.distance=28;
    view.nameLabel.distance=18;
    app.engine.addViews(diagram,[view]);
  }
  report.push({diagram:diagram.name,nodes:spec.nodes.length,edges:spec.edges.length});
}
app.project.save(OUTPUT_PATH);
%><%- JSON.stringify({file:OUTPUT_PATH,diagrams:report},null,2) %>
'''.replace('SPECS_PATH',json.dumps(str(OUT/'distribucion.json'))).replace('OUTPUT_PATH',json.dumps(str(target)))
    (OUT/'construir_vistas.ejs').write_text(script,encoding='utf-8')
    print(json.dumps({'base':str(OUT/'modelo_base.mdj'),'target':str(target),'classes':len(tables),'attributes':sum(len(t['columns']) for t in tables.values()),'relations':len(edges),'diagrams':len(specs),'routing':specs[0]['routing']},ensure_ascii=False))


if __name__=='__main__':main()
