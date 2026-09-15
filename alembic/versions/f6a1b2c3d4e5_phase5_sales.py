"""Add Phase 5 tables: ventas and detalles_venta

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-09-04 22:48:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'e5f6a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create Enums
    sale_type_enum = sa.Enum('PRESENCIAL', 'DIGITAL', name='sale_type')
    sale_status_enum = sa.Enum('PENDIENTE_PAGO', 'COMPLETADA', 'CANCELADA', name='sale_status')
    payment_method_enum = sa.Enum('EFECTIVO', 'TARJETA', 'QR', name='payment_method')

    # 2. Create ventas table
    op.create_table(
        'ventas',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('numero_recibo', sa.String(length=30), nullable=False),
        sa.Column('tipo', sale_type_enum, nullable=False, server_default='PRESENCIAL'),
        sa.Column('estado', sale_status_enum, nullable=False, server_default='COMPLETADA'),
        sa.Column('sucursal_id', sa.UUID(), nullable=False),
        sa.Column('cliente_id', sa.UUID(), nullable=True),
        sa.Column('cajero_id', sa.UUID(), nullable=True),
        sa.Column('metodo_pago', payment_method_enum, nullable=False, server_default='EFECTIVO'),
        sa.Column('referencia_pago', sa.String(length=100), nullable=True),
        sa.Column('monto_total', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0.0'),
        sa.Column('monto_recibido', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('cambio', sa.Numeric(precision=10, scale=2), nullable=True, server_default='0.0'),
        sa.Column('nota', sa.String(length=300), nullable=True),
        sa.Column('motivo_cancelacion', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['sucursal_id'], ['sucursales.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['cliente_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cajero_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ventas_id'), 'ventas', ['id'], unique=False)
    op.create_index(op.f('ix_ventas_numero_recibo'), 'ventas', ['numero_recibo'], unique=True)
    op.create_index(op.f('ix_ventas_sucursal_id'), 'ventas', ['sucursal_id'], unique=False)
    op.create_index(op.f('ix_ventas_cliente_id'), 'ventas', ['cliente_id'], unique=False)
    op.create_index(op.f('ix_ventas_cajero_id'), 'ventas', ['cajero_id'], unique=False)
    op.create_index(op.f('ix_ventas_estado'), 'ventas', ['estado'], unique=False)

    # 3. Create detalles_venta table
    op.create_table(
        'detalles_venta',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('venta_id', sa.UUID(), nullable=False),
        sa.Column('variante_id', sa.UUID(), nullable=False),
        sa.Column('reserva_id', sa.UUID(), nullable=True),
        sa.Column('cantidad', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('precio_unitario', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('subtotal', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['venta_id'], ['ventas.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variante_id'], ['variantes_producto.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['reserva_id'], ['reservas.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_detalles_venta_id'), 'detalles_venta', ['id'], unique=False)
    op.create_index(op.f('ix_detalles_venta_venta_id'), 'detalles_venta', ['venta_id'], unique=False)
    op.create_index(op.f('ix_detalles_venta_variante_id'), 'detalles_venta', ['variante_id'], unique=False)
    op.create_index(op.f('ix_detalles_venta_reserva_id'), 'detalles_venta', ['reserva_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_detalles_venta_reserva_id'), table_name='detalles_venta')
    op.drop_index(op.f('ix_detalles_venta_variante_id'), table_name='detalles_venta')
    op.drop_index(op.f('ix_detalles_venta_venta_id'), table_name='detalles_venta')
    op.drop_index(op.f('ix_detalles_venta_id'), table_name='detalles_venta')
    op.drop_table('detalles_venta')

    op.drop_index(op.f('ix_ventas_estado'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_cajero_id'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_cliente_id'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_sucursal_id'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_numero_recibo'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_id'), table_name='ventas')
    op.drop_table('ventas')

    # Drop enums
    payment_method_enum = sa.Enum('EFECTIVO', 'TARJETA', 'QR', name='payment_method')
    payment_method_enum.drop(op.get_bind(), checkfirst=True)

    sale_status_enum = sa.Enum('PENDIENTE_PAGO', 'COMPLETADA', 'CANCELADA', name='sale_status')
    sale_status_enum.drop(op.get_bind(), checkfirst=True)

    sale_type_enum = sa.Enum('PRESENCIAL', 'DIGITAL', name='sale_type')
    sale_type_enum.drop(op.get_bind(), checkfirst=True)
