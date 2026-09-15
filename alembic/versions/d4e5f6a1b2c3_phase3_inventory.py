"""Add Phase 3 tables: inventario and movimientos_inventario

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-09-04 22:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create inventario table
    op.create_table(
        'inventario',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('sucursal_id', sa.UUID(), nullable=False),
        sa.Column('variante_id', sa.UUID(), nullable=False),
        sa.Column('stock_actual', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('stock_reservado', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('stock_minimo', sa.Integer(), nullable=False, server_default=sa.text('5')),
        sa.Column('ubicacion', sa.String(length=50), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['sucursal_id'], ['sucursales.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variante_id'], ['variantes_producto.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sucursal_id', 'variante_id', name='uq_inventario_sucursal_variante')
    )
    op.create_index(op.f('ix_inventario_id'), 'inventario', ['id'], unique=False)
    op.create_index(op.f('ix_inventario_sucursal_id'), 'inventario', ['sucursal_id'], unique=False)
    op.create_index(op.f('ix_inventario_variante_id'), 'inventario', ['variante_id'], unique=False)

    # 2. Create movimientos_inventario table
    movement_type_enum = sa.Enum(
        'ENTRADA', 'SALIDA_VENTA', 'SALIDA_RESERVA', 'LIBERACION_RESERVA', 'AJUSTE', 'DEVOLUCION',
        name='movement_type'
    )
    op.create_table(
        'movimientos_inventario',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('inventario_id', sa.UUID(), nullable=False),
        sa.Column('tipo', movement_type_enum, nullable=False),
        sa.Column('cantidad', sa.Integer(), nullable=False),
        sa.Column('stock_antes', sa.Integer(), nullable=False),
        sa.Column('stock_despues', sa.Integer(), nullable=False),
        sa.Column('referencia', sa.String(length=100), nullable=True),
        sa.Column('nota', sa.String(length=300), nullable=True),
        sa.Column('usuario_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['inventario_id'], ['inventario.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['usuario_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_movimientos_inventario_id'), 'movimientos_inventario', ['id'], unique=False)
    op.create_index(op.f('ix_movimientos_inventario_inventario_id'), 'movimientos_inventario', ['inventario_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_movimientos_inventario_inventario_id'), table_name='movimientos_inventario')
    op.drop_index(op.f('ix_movimientos_inventario_id'), table_name='movimientos_inventario')
    op.drop_table('movimientos_inventario')
    
    # Drop enum if on postgresql
    movement_type_enum = sa.Enum(
        'ENTRADA', 'SALIDA_VENTA', 'SALIDA_RESERVA', 'LIBERACION_RESERVA', 'AJUSTE', 'DEVOLUCION',
        name='movement_type'
    )
    movement_type_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_inventario_variante_id'), table_name='inventario')
    op.drop_index(op.f('ix_inventario_sucursal_id'), table_name='inventario')
    op.drop_index(op.f('ix_inventario_id'), table_name='inventario')
    op.drop_table('inventario')
