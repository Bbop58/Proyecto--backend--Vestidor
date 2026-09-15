"""Add Phase 4 tables: reservas and detalles_reserva

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-09-04 22:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5f6a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create reservation_status enum
    status_enum = sa.Enum(
        'PENDIENTE', 'PREPARADA', 'RECOGIDA', 'CANCELADA', 'EXPIRADA', 'COMPLETADA',
        name='reservation_status'
    )

    # 2. Create reservas table
    op.create_table(
        'reservas',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('codigo', sa.String(length=30), nullable=False),
        sa.Column('cliente_id', sa.UUID(), nullable=False),
        sa.Column('sucursal_id', sa.UUID(), nullable=False),
        sa.Column('estado', status_enum, nullable=False, server_default='PENDIENTE'),
        sa.Column('fecha_hora_esperada', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_expiracion', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_recogida', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_estimado', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0.0'),
        sa.Column('nota', sa.String(length=300), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['cliente_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sucursal_id'], ['sucursales.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reservas_id'), 'reservas', ['id'], unique=False)
    op.create_index(op.f('ix_reservas_codigo'), 'reservas', ['codigo'], unique=True)
    op.create_index(op.f('ix_reservas_cliente_id'), 'reservas', ['cliente_id'], unique=False)
    op.create_index(op.f('ix_reservas_sucursal_id'), 'reservas', ['sucursal_id'], unique=False)
    op.create_index(op.f('ix_reservas_estado'), 'reservas', ['estado'], unique=False)

    # 3. Create detalles_reserva table
    op.create_table(
        'detalles_reserva',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('reserva_id', sa.UUID(), nullable=False),
        sa.Column('variante_id', sa.UUID(), nullable=False),
        sa.Column('cantidad', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('precio_unitario', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('subtotal', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['reserva_id'], ['reservas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variante_id'], ['variantes_producto.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_detalles_reserva_id'), 'detalles_reserva', ['id'], unique=False)
    op.create_index(op.f('ix_detalles_reserva_reserva_id'), 'detalles_reserva', ['reserva_id'], unique=False)
    op.create_index(op.f('ix_detalles_reserva_variante_id'), 'detalles_reserva', ['variante_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_detalles_reserva_variante_id'), table_name='detalles_reserva')
    op.drop_index(op.f('ix_detalles_reserva_reserva_id'), table_name='detalles_reserva')
    op.drop_index(op.f('ix_detalles_reserva_id'), table_name='detalles_reserva')
    op.drop_table('detalles_reserva')

    op.drop_index(op.f('ix_reservas_estado'), table_name='reservas')
    op.drop_index(op.f('ix_reservas_sucursal_id'), table_name='reservas')
    op.drop_index(op.f('ix_reservas_cliente_id'), table_name='reservas')
    op.drop_index(op.f('ix_reservas_codigo'), table_name='reservas')
    op.drop_index(op.f('ix_reservas_id'), table_name='reservas')
    op.drop_table('reservas')

    status_enum = sa.Enum(
        'PENDIENTE', 'PREPARADA', 'RECOGIDA', 'CANCELADA', 'EXPIRADA', 'COMPLETADA',
        name='reservation_status'
    )
    status_enum.drop(op.get_bind(), checkfirst=True)
