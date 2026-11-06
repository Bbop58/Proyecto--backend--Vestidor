"""Deployment readiness checks the actual schema, not just a running Python process."""
import os
import re
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.database import Base, engine

router=APIRouter()


def schema_compatible(connection):
    found={(row[0],row[1]) for row in connection.execute(text(
        'SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema()'
    ))}
    expected={(table.name,column.name) for table in Base.metadata.tables.values() for column in table.columns}
    from app.saas_integrity import schema_problems
    return bool(expected) and expected <= found and not any(schema_problems(connection).values())


@router.get('/health/ready',tags=['Health'])
def ready():
    try:
        with engine.connect() as connection:
            if not schema_compatible(connection):
                return JSONResponse(status_code=503,content={'status':'not_ready','reason':'schema_incompatible'})
    except Exception:
        return JSONResponse(status_code=503,content={'status':'not_ready','reason':'database_unavailable'})
    commit=os.environ.get('RAILWAY_GIT_COMMIT_SHA','')
    return {'status':'ready','database':'reachable','schema':'compatible',
            'commit':commit if re.fullmatch(r'[0-9a-f]{40}',commit) else None}
