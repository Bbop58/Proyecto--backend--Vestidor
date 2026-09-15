from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.api.v1.router import api_v1_router

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
