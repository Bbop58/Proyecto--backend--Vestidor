import hashlib
import json
import uuid
from datetime import datetime,timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app.models import AnalyticsModel, Role, Permission
from app.ml.analytics_store import make_metadata
from app.services.backup_service import BackupService,json_serializer
from app.core.tenant_scope import bind_tenant
from tests.test_tenant_sales import shops


def model(company):
    return AnalyticsModel(tenant_id=company,kind='FORECAST',artifact=b'QA',
        artifact_sha256=hashlib.sha256(b'QA').hexdigest(),result={'forecast':90},
        model_metadata=make_metadata('0'*64,datetime(2026,1,1,tzinfo=timezone.utc)))


def test_restore_invalidates_only_own_models_and_never_exports_artifacts(db_session,shops):
    db=db_session;a=shops['a'][0];b=shops['b'][0]
    db.add_all([model(a.tenant_id),model(b.tenant_id)]);db.commit()
    bind_tenant(db,a.tenant_id,a.id)
    saved=json.loads(json.dumps(BackupService.create_backup(db,a),default=json_serializer))
    assert saved['version']=='4.6.0' and 'analytics_models' not in saved['data']
    assert BackupService.restore_backup(db,saved,a)['success']
    connection=db.connection()
    assert connection.execute(select(AnalyticsModel.id).where(AnalyticsModel.tenant_id==a.tenant_id)).first() is None
    assert connection.execute(select(AnalyticsModel.id).where(AnalyticsModel.tenant_id==b.tenant_id)).first()


def test_failure_after_invalidation_rolls_models_and_business_back(db_session,shops,monkeypatch):
    from app.services.audit_service import AuditService
    db=db_session;a=shops['a'][0]
    db.add(model(a.tenant_id));db.commit();bind_tenant(db,a.tenant_id,a.id)
    saved=json.loads(json.dumps(BackupService.create_backup(db,a),default=json_serializer))
    def fail(*args,**kwargs):raise RuntimeError('synthetic final audit failure')
    monkeypatch.setattr(AuditService,'record',fail)
    with pytest.raises(HTTPException):BackupService.restore_backup(db,saved,a)
    assert db.connection().execute(select(AnalyticsModel.id).where(AnalyticsModel.tenant_id==a.tenant_id)).first()


@pytest.mark.parametrize('role_name,expected',[('admin',503),('encargado',403),('cajero',403),('cliente',403)])
def test_training_requires_separate_permission(client,db_session,shops,monkeypatch,role_name,expected):
    from app.main import app
    from app.api.deps import get_current_user
    from app.ml.ml_service import MLAnalyticsService
    a=shops['a'][0];role=db_session.query(Role).filter_by(name=role_name,tenant_id=None).one()
    a.role_id=role.id;db_session.commit();db_session.expire_all()
    app.dependency_overrides[get_current_user]=lambda:a
    def blocked_training(*args,**kwargs):raise HTTPException(503,'Diagnostic only; no training')
    monkeypatch.setattr(MLAnalyticsService,'train_all',blocked_training)
    response=client.post('/api/v1/analytics/train')
    assert response.status_code==expected


def test_repeated_permission_seed_does_not_grant_readers_training(db_session,shops):
    from app.seed_permissions import seed
    seed(db_session,include_seasons=False);seed(db_session,include_seasons=False)
    db_session.expire_all()
    for name in ['admin','encargado','cajero','cliente']:
        role=db_session.query(Role).filter_by(name=name,tenant_id=None).one()
        assert ('analytics.train' in {p.code for p in role.permissions}) == (name=='admin')
