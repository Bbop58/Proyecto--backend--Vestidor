"""Add Phase 2 tables: proveedores and temporadas

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-09-04 22:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a1b2'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create proveedores table
    op.create_table(
        'proveedores',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('contacto', sa.String(length=100), nullable=True),
        sa.Column('telefono', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('direccion', sa.String(length=200), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_proveedores_id'), 'proveedores', ['id'], unique=False)
    op.create_index(op.f('ix_proveedores_nombre'), 'proveedores', ['nombre'], unique=True)

    # 2. Create temporadas table
    op.create_table(
        'temporadas',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('nombre', sa.String(length=50), nullable=False),
        sa.Column('año', sa.Integer(), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_fin', sa.Date(), nullable=False),
        sa.Column('activa', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_temporadas_año'), 'temporadas', ['año'], unique=False)
    op.create_index(op.f('ix_temporadas_id'), 'temporadas', ['id'], unique=False)
    op.create_index(op.f('ix_temporadas_nombre'), 'temporadas', ['nombre'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_temporadas_nombre'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_id'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_año'), table_name='temporadas')
    op.drop_table('temporadas')

    op.drop_index(op.f('ix_proveedores_nombre'), table_name='proveedores')
    op.drop_index(op.f('ix_proveedores_id'), table_name='proveedores')
    op.drop_table('proveedores')
