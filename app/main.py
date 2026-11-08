import app.core.tenant_scope
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.api.v1.router import api_v1_router
from app.database import engine, Base
import app.models
from app.api.health import router as health_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="API REST Full-Stack con Autenticacion JWT, FastAPI, PostgreSQL, Angular y Flutter",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# CORS Configuration
origins = [str(o) for o in settings.BACKEND_CORS_ORIGINS]
for extra_origin in ["http://localhost:8080", "http://127.0.0.1:8080"]:
    if extra_origin not in origins:
        origins.append(extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 Router
app.include_router(api_v1_router, prefix=settings.API_V1_STR)
app.include_router(health_router)

# Mount static files directory for product images and assets
import os
from fastapi.staticfiles import StaticFiles
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(settings.API_V1_STR + '/auth/mobile/'):
        # FastAPI's default validation errors may echo the entire request body.
        return JSONResponse(status_code=422, content={'detail': 'Solicitud de sesión móvil no válida'},
                            headers={'Cache-Control': 'no-store'})
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Starlette's outer ServerErrorMiddleware executes this handler outside
    # CORSMiddleware. Preserve the same explicit origin policy on 500s.
    origin = request.headers.get('origin')
    headers = {}
    if origin in origins:
        headers = {'Access-Control-Allow-Origin': origin,
                   'Access-Control-Allow-Credentials': 'true', 'Vary': 'Origin'}
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
        headers=headers,
    )


@app.on_event("startup")
def on_startup():
    if not settings.RUN_STARTUP_MIGRATIONS:
        return
    Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS impuesto_iva NUMERIC(10, 2) DEFAULT 0.0 NOT NULL;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS monto_neto NUMERIC(10, 2) DEFAULT 0.0 NOT NULL;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS costo_envio NUMERIC(10, 2) DEFAULT 0.0;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS direccion_envio VARCHAR(300);
                
                -- Columnas Branding Tenant y Pasarela Multi-Tenant
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS color_primario VARCHAR(20) DEFAULT '#4f46e5';
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS color_secundario VARCHAR(20) DEFAULT '#818cf8';
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS eslogan VARCHAR(200);
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS paypal_client_id VARCHAR(255);
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS paypal_client_secret VARCHAR(255);
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS paypal_mode VARCHAR(20) DEFAULT 'sandbox';

                -- Columnas Multitenant (tenant_id)
                ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;
                ALTER TABLE sucursales ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE categorias ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE temporadas ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE inventario ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE reservas ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE movimientos_inventario ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(50) DEFAULT '127.0.0.1';
                ALTER TABLE cambios_producto ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE favoritos ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE tarifas_delivery ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;

                -- Ofertas y Descuentos
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS precio_oferta NUMERIC(10, 2);
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS en_oferta BOOLEAN DEFAULT FALSE;
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS fecha_inicio_oferta TIMESTAMPTZ;
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS fecha_fin_oferta TIMESTAMPTZ;
                ALTER TABLE productos ADD COLUMN IF NOT EXISTS motivo_oferta VARCHAR(250);
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS monto_subtotal NUMERIC(10, 2) DEFAULT 0.0;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS descuento NUMERIC(10, 2) DEFAULT 0.0;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS codigo_cupon VARCHAR(50);

                -- Homogeneización 100% Masculina y Fix de Imágenes
                UPDATE productos 
                SET nombre = 'Smoking de Gala Black Tie',
                    descripcion = 'Smoking formal para hombre con solapa chal de satén y corte entallado, ideal para eventos de gala, bodas y etiqueta rigurosa.',
                    precio_base = 450.00,
                    imagen_url = 'https://images.unsplash.com/photo-1507679799987-c73779587ccf?w=600'
                WHERE nombre = 'Vestido de Fiesta Elegante';

                UPDATE productos
                SET imagen_url = 'https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=600'
                WHERE nombre = 'Camisa Ejecutiva Slim Fit';

                UPDATE variantes_producto
                SET talla = '38R', color = 'Negro Clásico', sku = 'SMK-GLA-BLK-38', precio_extra = 0.0
                WHERE sku = 'VES-FST-RED-S';

                UPDATE variantes_producto
                SET talla = '40R', color = 'Negro Clásico', sku = 'SMK-GLA-BLK-40', precio_extra = 0.0
                WHERE sku = 'VES-FST-RED-M';

                UPDATE variantes_producto
                SET talla = '42R', color = 'Azul Noche', sku = 'SMK-GLA-NVY-42', precio_extra = 25.0
                WHERE sku = 'VES-FST-BLK-L';

                UPDATE categorias
                SET descripcion = 'Prendas de vestir formales masculinas, trajes ejecutivos, ternos y blazers elegantes'
                WHERE nombre = 'Moda Formal y Ejecutiva';

                CREATE TABLE IF NOT EXISTS cupones_descuento (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    codigo VARCHAR(50) NOT NULL,
                    descripcion VARCHAR(200),
                    porcentaje_descuento NUMERIC(5, 2) NOT NULL DEFAULT 10.00,
                    monto_minimo NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
                    fecha_vencimiento TIMESTAMP WITH TIME ZONE,
                    activo BOOLEAN NOT NULL DEFAULT TRUE,
                    usos_actuales INTEGER NOT NULL DEFAULT 0,
                    max_usos INTEGER,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_wallets (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    saldo_actual NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
                    saldo_retenido NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
                    nonce_secuencia INTEGER NOT NULL DEFAULT 0,
                    firma_balance VARCHAR(255),
                    moneda VARCHAR(10) NOT NULL DEFAULT 'BOB',
                    ultimo_sync_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_user_tenant_wallet UNIQUE (user_id, tenant_id)
                );

                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS saldo_actual NUMERIC(10, 2) DEFAULT 0.00;
                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS saldo_retenido NUMERIC(10, 2) DEFAULT 0.00;
                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS nonce_secuencia INTEGER DEFAULT 0;
                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS firma_balance VARCHAR(255);
                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS moneda VARCHAR(10) DEFAULT 'BOB';
                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS ultimo_sync_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
                ALTER TABLE user_wallets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id UUID PRIMARY KEY,
                    wallet_id UUID NOT NULL REFERENCES user_wallets(id) ON DELETE CASCADE,
                    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    tipo VARCHAR(50) NOT NULL,
                    monto NUMERIC(10, 2) NOT NULL,
                    saldo_anterior NUMERIC(10, 2) NOT NULL,
                    saldo_posterior NUMERIC(10, 2) NOT NULL,
                    referencia_externa VARCHAR(150),
                    client_operation_id UUID,
                    origen VARCHAR(30) NOT NULL DEFAULT 'MOBILE',
                    estado_sync VARCHAR(30) NOT NULL DEFAULT 'CONCILIADO',
                    notas TEXT,
                    metadatos JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """))
    except Exception as e:
        print(f"[STARTUP MIGRATION ERROR]: {e}")

    import threading
    def auto_seed():
        try:
            from app.seed_database import seed_all
            seed_all()
        except Exception as e:
            print(f"[AUTO-SEED] Aviso: {e}")
    if settings.RUN_DEMO_SEED:
        threading.Thread(target=auto_seed, daemon=True).start()



@app.get("/health", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "version": "1.0.0"
    }


@app.get("/", include_in_schema=False)
def root():
    return {
        "message": f"Bienvenido a la API de {settings.PROJECT_NAME}",
        "docs": "/docs",
        "health": "/health"
    }
