"""Add roles and permissions tables and user role_id FK

Revision ID: a1b2c3d4e5f6
Revises: 86186ef92c18
Create Date: 2026-09-04 10:00:00.000000

"""
import uuid
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '86186ef92c18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create roles table
    op.create_table(
        'roles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_roles_id'), 'roles', ['id'], unique=False)
    op.create_index(op.f('ix_roles_name'), 'roles', ['name'], unique=True)

    # 2. Create permissions table
    op.create_table(
        'permissions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(length=100), nullable=False),
        sa.Column('module', sa.String(length=50), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_permissions_code'), 'permissions', ['code'], unique=True)
    op.create_index(op.f('ix_permissions_id'), 'permissions', ['id'], unique=False)

    # 3. Create role_permissions table
    op.create_table(
        'role_permissions',
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('permission_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('role_id', 'permission_id')
    )

    # 4. Add nullable role_id column to users
    op.add_column('users', sa.Column('role_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_users_role_id_roles', 'users', 'roles', ['role_id'], ['id'])

    # 5. Data migration: create admin role and assign to existing users
    admin_role_id = str(uuid.uuid4())
    op.execute(
        f"""
        INSERT INTO roles (id, name, description, created_at, updated_at)
        VALUES ('{admin_role_id}', 'admin', 'Administrador del sistema', NOW(), NOW())
        ON CONFLICT (name) DO NOTHING;
        """
    )
    op.execute(
        f"""
        UPDATE users
        SET role_id = (SELECT id FROM roles WHERE name = 'admin' LIMIT 1)
        WHERE role_id IS NULL;
        """
    )

    # 6. Set role_id to NOT NULL
    op.alter_column('users', 'role_id', nullable=False)


def downgrade() -> None:
    op.drop_constraint('fk_users_role_id_roles', 'users', type_='foreignkey')
    op.drop_column('users', 'role_id')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_permissions_id'), table_name='permissions')
    op.drop_index(op.f('ix_permissions_code'), table_name='permissions')
    op.drop_table('permissions')
    op.drop_index(op.f('ix_roles_name'), table_name='roles')
    op.drop_index(op.f('ix_roles_id'), table_name='roles')
    op.drop_table('roles')
