from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.api.v1.router import api_v1_router
from app.database import engine, Base
import app.models

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
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API v1 Router
app.include_router(api_v1_router, prefix=settings.API_V1_STR)

# Mount static files directory for product images and assets
import os
from fastapi.staticfiles import StaticFiles
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS impuesto_iva NUMERIC(10, 2) DEFAULT 0.0 NOT NULL;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS monto_neto NUMERIC(10, 2) DEFAULT 0.0 NOT NULL;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS costo_envio NUMERIC(10, 2) DEFAULT 0.0;
                ALTER TABLE ventas ADD COLUMN IF NOT EXISTS direccion_envio VARCHAR(300);
                
                -- Columnas Branding Tenant
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS color_primario VARCHAR(20) DEFAULT '#4f46e5';
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS color_secundario VARCHAR(20) DEFAULT '#818cf8';
                ALTER TABLE tenants ADD COLUMN IF NOT EXISTS eslogan VARCHAR(200);

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
                ALTER TABLE cambios_producto ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE favoritos ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
                ALTER TABLE tarifas_delivery ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
            """))
    except Exception as e:
        pass

    import threading
    def auto_seed():
        try:
            from app.seed_database import seed_all
            seed_all()
        except Exception as e:
            print(f"[AUTO-SEED] Aviso: {e}")
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
