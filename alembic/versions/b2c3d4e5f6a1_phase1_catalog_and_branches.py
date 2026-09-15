"""Add Phase 1 tables: sucursales, categorias, productos, variantes_producto

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-09-04 22:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a1'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create sucursales table
    op.create_table(
        'sucursales',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('ciudad', sa.String(length=50), nullable=False),
        sa.Column('direccion', sa.String(length=200), nullable=False),
        sa.Column('telefono', sa.String(length=20), nullable=True),
        sa.Column('activa', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sucursales_id'), 'sucursales', ['id'], unique=False)
    op.create_index(op.f('ix_sucursales_nombre'), 'sucursales', ['nombre'], unique=True)

    # 2. Create categorias table
    op.create_table(
        'categorias',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('nombre', sa.String(length=50), nullable=False),
        sa.Column('descripcion', sa.String(length=200), nullable=True),
        sa.Column('activa', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_categorias_id'), 'categorias', ['id'], unique=False)
    op.create_index(op.f('ix_categorias_nombre'), 'categorias', ['nombre'], unique=True)

    # 3. Create productos table
    op.create_table(
        'productos',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('precio_base', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('categoria_id', sa.UUID(), nullable=False),
        sa.Column('temporada', sa.String(length=50), nullable=True),
        sa.Column('proveedor', sa.String(length=100), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_productos_id'), 'productos', ['id'], unique=False)
    op.create_index(op.f('ix_productos_nombre'), 'productos', ['nombre'], unique=False)
    op.create_index(op.f('ix_productos_temporada'), 'productos', ['temporada'], unique=False)
    op.create_index(op.f('ix_productos_proveedor'), 'productos', ['proveedor'], unique=False)

    # 4. Create variantes_producto table
    op.create_table(
        'variantes_producto',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('producto_id', sa.UUID(), nullable=False),
        sa.Column('talla', sa.String(length=10), nullable=False),
        sa.Column('color', sa.String(length=30), nullable=False),
        sa.Column('sku', sa.String(length=50), nullable=False),
        sa.Column('precio_extra', sa.Numeric(precision=10, scale=2), nullable=False, server_default=sa.text('0.00')),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['producto_id'], ['productos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('producto_id', 'talla', 'color', name='uq_producto_talla_color')
    )
    op.create_index(op.f('ix_variantes_producto_id'), 'variantes_producto', ['id'], unique=False)
    op.create_index(op.f('ix_variantes_producto_producto_id'), 'variantes_producto', ['producto_id'], unique=False)
    op.create_index(op.f('ix_variantes_producto_sku'), 'variantes_producto', ['sku'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_variantes_producto_sku'), table_name='variantes_producto')
    op.drop_index(op.f('ix_variantes_producto_producto_id'), table_name='variantes_producto')
    op.drop_index(op.f('ix_variantes_producto_id'), table_name='variantes_producto')
    op.drop_table('variantes_producto')

    op.drop_index(op.f('ix_productos_proveedor'), table_name='productos')
    op.drop_index(op.f('ix_productos_temporada'), table_name='productos')
    op.drop_index(op.f('ix_productos_nombre'), table_name='productos')
    op.drop_index(op.f('ix_productos_id'), table_name='productos')
    op.drop_table('productos')

    op.drop_index(op.f('ix_categorias_nombre'), table_name='categorias')
    op.drop_index(op.f('ix_categorias_id'), table_name='categorias')
    op.drop_table('categorias')

    op.drop_index(op.f('ix_sucursales_nombre'), table_name='sucursales')
    op.drop_index(op.f('ix_sucursales_id'), table_name='sucursales')
    op.drop_table('sucursales')
