import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import require_permission
from app.models.user import User
from app.services.backup_service import BackupService, json_serializer
from app.schemas.backup import AutomaticBackupUpdate
from app.services import automatic_backup_service as automatic
from app.services.backup_storage import StorageError
from uuid import UUID

router = APIRouter(prefix="/backups", tags=["Copias de Seguridad y Restauración (Backup/Restore)"])


@router.get('/automatic')
def automatic_config(db: Session = Depends(get_db),
                     actor: User = Depends(require_permission('backups.download'))):
    return {**automatic.policy_summary(automatic.get_policy(db, actor)),
            'storage_ready': bool(automatic.storage_ready())}


@router.put('/automatic')
def configure_automatic(payload: AutomaticBackupUpdate, db: Session = Depends(get_db),
                        actor: User = Depends(require_permission('backups.configure'))):
    return {**automatic.policy_summary(automatic.update_policy(db, actor, payload)),
            'storage_ready': bool(automatic.storage_ready())}


@router.get('/automatic/copies')
def automatic_copies(db: Session = Depends(get_db),
                     actor: User = Depends(require_permission('backups.download'))):
    return automatic.list_copies(db, actor)


@router.get('/automatic/copies/{copy_id}/download')
def automatic_download(copy_id: UUID, db: Session = Depends(get_db),
                       actor: User = Depends(require_permission('backups.download'))):
    try:
        content = automatic.download_copy(db, actor, copy_id)
    except StorageError:
        raise HTTPException(503, 'No se pudo recuperar la copia; vuelve a intentarlo')
    return Response(content, media_type='application/json', headers={
        'Content-Disposition': f'attachment; filename="backup_automatic_{copy_id}.json"'})


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
    current_user: User = Depends(require_permission("backups.download"))
):
    """
    Genera un snapshot consistente de la empresa autenticada con checksum SHA-256
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
    current_user: User = Depends(require_permission("backups.restore"))
):
    """
    Recibe un archivo de respaldo .json generado previamente, valida su integridad
    y restaura la empresa autenticada en una transacción atómica.
    """
    from app.core.access_policy import resolve_access_scope, AccessScope
    if resolve_access_scope(current_user) != AccessScope.COMPANY:
        raise HTTPException(403,"La restauración requiere permiso del negocio y ámbito de empresa")
    if not (file.filename or '').endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de archivo no válido. Se requiere un archivo JSON (.json)."
        )

    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413,"El respaldo supera el límite de 10 MB")
        backup_dict = json.loads(content.decode("utf-8"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo leer el archivo JSON de respaldo"
        )

    return BackupService.restore_backup(
        db=db,
        backup_content=backup_dict,
        current_user=current_user
    )

