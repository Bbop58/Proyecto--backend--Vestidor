"""Versioned SaaS offers. No writes to Subscription or legacy contracted terms."""
from contextlib import contextmanager
from datetime import datetime,timezone
from sqlalchemy import select,func,insert,update
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.core.access_policy import resolve_access_scope,AccessScope
from app.core.plan_features import PLAN_FEATURES,feature_values
from app.models.subscription_plan import SubscriptionPlan as Plan
from app.models.subscription_plan_version import SubscriptionPlanVersion as Version
from app.schemas.plan_catalog import PlanVersionResponse
from app.services.platform_audit_service import PlatformAuditService


class PlanCatalogService:
    @staticmethod
    def _authorize(db,actor):
        if resolve_access_scope(actor)!=AccessScope.PLATFORM or db.info.get('platform_actor_id')!=actor.id:
            raise HTTPException(403,'Se requiere contexto de plataforma verificado')

    @classmethod
    @contextmanager
    def _write(cls,db,actor):
        cls._authorize(db,actor)
        try:
            yield db.connection()
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409,'El catálogo cambió o contiene una referencia duplicada/incompatible')
        except Exception:
            db.rollback();raise

    @staticmethod
    def _plan(connection,plan_id,lock=False):
        query=select(Plan.id,Plan.codigo,Plan.nombre,Plan.activo).where(Plan.id==plan_id)
        row=connection.execute(query.with_for_update() if lock else query).mappings().first()
        if not row:raise HTTPException(404,'Plan no disponible')
        return dict(row)

    @staticmethod
    def _row(connection,version_id,lock=False):
        query=select(Version.__table__).where(Version.id==version_id)
        row=connection.execute(query.with_for_update() if lock else query).mappings().first()
        if not row:raise HTTPException(404,'Revisión no disponible')
        return dict(row)

    @staticmethod
    def _json(row):
        fields={key:row[key] for key in PlanVersionResponse.model_fields if key in row}
        fields.update(features=feature_values(row),es_gratuito=row['precio_mensual_bob']==0)
        return PlanVersionResponse.model_validate(fields).model_dump(mode='json')

    @staticmethod
    def _conditions(request):
        return {'precio_mensual_bob':request.precio_mensual_bob,'max_sucursales':request.max_sucursales,
            'moneda':request.moneda,'periodicidad':request.periodicidad,'descripcion':request.descripcion,
            **{column:request.features[key] for key,column in PLAN_FEATURES.items()}}

    @staticmethod
    def _audit(db,actor,action,target,before,after,reason,kind='PLAN_VERSION'):
        PlatformAuditService.record(db,actor,action,kind,target,before,after,reason)

    @classmethod
    def create_plan(cls,db,actor,request):
        with cls._write(db,actor) as connection:
            # Legacy fields are not the new offer; only a published version is.
            plan_id=connection.execute(insert(Plan).values(codigo=request.codigo,nombre=request.nombre,
                max_sucursales=1,precio_mensual_bob=0,permite_reportes_ia=False,
                permite_offline=False,activo=True,catalog_managed=True).returning(Plan.id)).scalar_one()
            result=cls._plan(connection,plan_id)
            audit={**result,'id':str(result['id'])}
            cls._audit(db,actor,'PLAN_CREATE',plan_id,{},audit,request.reason,'PLAN')
            return result

    @classmethod
    def list_plans(cls,db,actor,limit=50,offset=0):
        cls._authorize(db,actor)
        return [dict(row) for row in db.connection().execute(select(Plan.id,Plan.codigo,Plan.nombre,Plan.activo)
            .order_by(Plan.codigo).limit(limit).offset(offset)).mappings()]

    @classmethod
    def create_draft(cls,db,actor,plan_id,request):
        with cls._write(db,actor) as connection:
            plan=cls._plan(connection,plan_id,lock=True)
            if not plan['activo']:raise HTTPException(409,'El plan no está disponible para nuevas revisiones')
            revision=connection.execute(select(func.coalesce(func.max(Version.revision),0)+1)
                .where(Version.plan_id==plan_id)).scalar_one()
            version_id=connection.execute(insert(Version).values(plan_id=plan_id,revision=revision,
                **cls._conditions(request)).returning(Version.id)).scalar_one()
            result=cls._json(cls._row(connection,version_id))
            cls._audit(db,actor,'PLAN_DRAFT_CREATE',version_id,{},result,request.reason)
            return result

    @classmethod
    def update_draft(cls,db,actor,version_id,request):
        with cls._write(db,actor) as connection:
            row=cls._row(connection,version_id,lock=True)
            if row['estado']!='BORRADOR':raise HTTPException(409,'Las condiciones publicadas son inmutables; crea otra revisión')
            if row['lock_version']!=request.expected_version:raise HTTPException(409,'El borrador cambió; actualiza antes de guardar')
            before=cls._json(row)
            connection.execute(update(Version).where(Version.id==version_id).values(
                **cls._conditions(request),lock_version=row['lock_version']+1))
            result=cls._json(cls._row(connection,version_id))
            cls._audit(db,actor,'PLAN_DRAFT_UPDATE',version_id,before,result,request.reason)
            return result

    @classmethod
    def _transition(cls,db,actor,version_id,request,state):
        with cls._write(db,actor) as connection:
            row=cls._row(connection,version_id,lock=True)
            if row['estado']==state:return cls._json(row)
            expected='BORRADOR' if state=='PUBLICADA' else 'PUBLICADA'
            if row['estado']!=expected:raise HTTPException(409,'Transición de revisión no permitida')
            if row['lock_version']!=request.expected_version:raise HTTPException(409,'La revisión cambió; actualiza antes de confirmar')
            if not cls._plan(connection,row['plan_id'])['activo'] and state=='PUBLICADA':
                raise HTTPException(409,'El plan no está activo')
            before=cls._json(row)
            values={'estado':state,'lock_version':row['lock_version']+1}
            if state=='PUBLICADA':values['published_at']=datetime.now(timezone.utc)
            connection.execute(update(Version).where(Version.id==version_id).values(**values))
            result=cls._json(cls._row(connection,version_id))
            cls._audit(db,actor,'PLAN_PUBLISH' if state=='PUBLICADA' else 'PLAN_RETIRE',
                       version_id,before,result,request.reason)
            return result

    @classmethod
    def publish_version(cls,db,actor,version_id,request):
        return cls._transition(db,actor,version_id,request,'PUBLICADA')

    @classmethod
    def retire_version(cls,db,actor,version_id,request):
        return cls._transition(db,actor,version_id,request,'RETIRADA')

    @classmethod
    def get_version(cls,db,actor,version_id):
        cls._authorize(db,actor)
        return cls._json(cls._row(db.connection(),version_id))

    @classmethod
    def list_versions(cls,db,actor,plan_id,limit=50,offset=0):
        cls._authorize(db,actor);connection=db.connection();cls._plan(connection,plan_id)
        return [cls._json(row) for row in connection.execute(select(Version.__table__)
            .where(Version.plan_id==plan_id).order_by(Version.revision.desc()).limit(limit).offset(offset)).mappings()]

    @classmethod
    def list_published(cls,db):
        # A retired latest offer must not resurrect the previous lower price.
        latest=select(Version.plan_id,func.max(Version.revision).label('revision'))\
            .where(Version.estado!='BORRADOR').group_by(Version.plan_id).subquery()
        query=select(Version.__table__,Plan.codigo,Plan.nombre).join(Plan,Plan.id==Version.plan_id).join(
            latest,(latest.c.plan_id==Version.plan_id)&(latest.c.revision==Version.revision))\
            .where(Plan.activo.is_(True),Version.estado=='PUBLICADA').order_by(Version.max_sucursales,Plan.codigo)
        offers=[]
        for row in db.connection().execute(query).mappings():
            offers.append({'id':row['plan_id'],'codigo':row['codigo'],'nombre':row['nombre'],
                'descripcion':row['descripcion'],'max_sucursales':row['max_sucursales'],
                'permite_reportes_ia':row['permite_reportes_ia'],'permite_offline':row['permite_offline'],
                'precio_mensual_bob':row['precio_mensual_bob'],'activo':True,
                'version_id':row['id'],'revision':row['revision'],'moneda':row['moneda'],
                'periodicidad':row['periodicidad'],'features':feature_values(row),
                'es_gratuito':row['precio_mensual_bob']==0,'origen':row['origen']})
        return offers
