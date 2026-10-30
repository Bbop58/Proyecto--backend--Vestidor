import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission
from app.models.user import User
from app.services.backup_service import BackupService, json_serializer

router = APIRouter(prefix="/backups", tags=["Copias de Seguridad y Restauración (Backup/Restore)"])


@router.get("/stats", summary="Obtener estadísticas de entidades de la base de datos")
def get_backup_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("roles.list"))
):
    """
    Retorna el conteo de registros por entidad principal del sistema.
    """
    return BackupService.get_system_stats(db)


@router.get("/download", summary="Generar y descargar copia de seguridad del sistema")
def download_backup(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("roles.create"))
):
    """
    Genera un snapshot completo consistente del sistema con checksum SHA-256
    y lo retorna como archivo descargable .json.
    """
    backup_data = BackupService.create_backup(db=db, current_user=current_user)
    json_bytes = json.dumps(backup_data, default=json_serializer, indent=2).encode("utf-8")
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_ficctstore_{timestamp}.json"

    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post("/restore", summary="Restaurar base de datos a partir de un archivo JSON")
async def restore_system(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("roles.create"))
):
    """
    Recibe un archivo de respaldo .json generado previamente, valida su integridad
    y restaura el estado del sistema en una transacción atómica.
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de archivo no válido. Se requiere un archivo JSON (.json)."
        )

    try:
        content = await file.read()
        backup_dict = json.loads(content.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo leer el archivo JSON: {str(e)}"
        )

    return BackupService.restore_backup(
        db=db,
        backup_content=backup_dict,
        current_user=current_user
    )

