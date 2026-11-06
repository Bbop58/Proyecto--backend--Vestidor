import uuid
from pathlib import Path
import pytest
from sqlalchemy import text, select
from app.models.tenant import Tenant
from app.models.user import User
from app.models.notification import Notification
from app.models.role import Role
from app.models.role_permission import role_permissions
from app.api.deps import get_authenticated_identity
from app.models.sale import Sale
from fastapi import HTTPException
from tests.support_saas import saas_actors, headers
from scripts.bootstrap_platform_operator import provision, BootstrapError


def test_migration_upgrades_legacy_metadata_and_is_repeatable(db_session):
    connection=db_session.connection()
    connection.exec_driver_sql('DROP TABLE platform_audit_logs')
    connection.exec_driver_sql('ALTER TABLE tenants DROP COLUMN es_operadora')
    connection.exec_driver_sql('ALTER TABLE tenants DROP COLUMN platform_revision')
    tenant_id=uuid.uuid4();admin_id=uuid.uuid4();custom_id=uuid.uuid4()
    connection.execute(text("INSERT INTO tenants(id,nombre,razon_social,nit_rut,slug,activo,created_at,updated_at) VALUES (:id,'Legacy','Legacy','1234','legacy',true,now(),now())"),{'id':tenant_id})
    connection.execute(text("INSERT INTO roles(id,tenant_id,name,created_at,updated_at) VALUES (:a,NULL,'admin',now(),now()),(:c,:t,'custom',now(),now())"),{'a':admin_id,'c':custom_id,'t':tenant_id})
    sql=(Path(__file__).parents[1]/'migrations/saas_01_platform_scope.sql').read_text(encoding='utf-8')
    connection.exec_driver_sql(sql);connection.exec_driver_sql(sql)
    row=connection.execute(text('SELECT nombre,es_operadora,platform_revision FROM tenants WHERE id=:id'),{'id':tenant_id}).one()
    assert tuple(row)==('Legacy',False,0)
    assert connection.execute(text('SELECT count(*) FROM role_permissions WHERE role_id=:id'),{'id':admin_id}).scalar_one()==2
    assert connection.execute(text('SELECT count(*) FROM role_permissions WHERE role_id=:id'),{'id':custom_id}).scalar_one()==0


def test_bootstrap_dry_run_and_repeat_preserve_commercial_roles(db_session,saas_actors):
    db=db_session;user=saas_actors['customer'];before=(user.tenant_id,user.role_id)
    admin_role=saas_actors['admin'].role_id
    before_permissions=db.query(Role).filter_by(id=admin_role).one().permissions[:]
    report=provision(db.connection(),user.id,'saas-operator',False)
    assert report['mode']=='dry-run'
    db.expire_all()
    assert (user.tenant_id,user.role_id)==before
    provision(db.connection(),user.id,'saas-operator',True)
    db.expire_all()
    assert user.tenant_id==saas_actors['operator_company'].id
    assert user.role.name=='superadmin_plataforma'
    assert user.role.permissions==[]
    assert set(p.code for p in db.get(Role,admin_role).permissions)==set(p.code for p in before_permissions)
    again=provision(db.connection(),user.id,'saas-operator',True)
    assert again['already_assigned'] is True


def test_bootstrap_cannot_move_account_with_history_or_convert_commercial_company(db_session,saas_actors):
    user=saas_actors['customer'];original=user.tenant_id
    db_session.add(Notification(user_id=user.id,title='Test',message='Synthetic',type='GENERAL'))
    db_session.flush()
    with pytest.raises(BootstrapError):provision(db_session.connection(),user.id,'saas-operator',True)
    with pytest.raises(BootstrapError):provision(db_session.connection(),saas_actors['cashier'].id,'saas-a',True)
    db_session.expire_all()
    assert user.tenant_id==original


def test_platform_orm_context_rejects_private_models_and_raw_sql(db_session,saas_actors):
    actor=saas_actors['operator']
    get_authenticated_identity(db_session,{'sub':str(actor.id),'tenant_id':str(actor.tenant_id)})
    with pytest.raises(HTTPException):db_session.query(Sale).count()
    with pytest.raises(HTTPException):db_session.execute(text('SELECT * FROM ventas'))


def test_operator_profile_keeps_global_email_uniqueness(client,saas_actors):
    response=client.put('/api/v1/users/me',headers=headers(saas_actors['operator']),
                        json={'email':saas_actors['customer'].email})
    assert response.status_code==400


def test_commercial_context_cannot_read_or_write_platform_audit(db_session,saas_actors):
    from app.models.platform_audit_log import PlatformAuditLog
    actor=saas_actors['admin']
    get_authenticated_identity(db_session,{'sub':str(actor.id),'tenant_id':str(actor.tenant_id)})
    with pytest.raises(HTTPException):db_session.query(PlatformAuditLog).all()
    db_session.add(PlatformAuditLog(actor_id=actor.id,action='FORGED',target_type='IDENTITY',
        target_id=actor.id,reason='Synthetic forbidden audit'))
    with pytest.raises(HTTPException):db_session.flush()
