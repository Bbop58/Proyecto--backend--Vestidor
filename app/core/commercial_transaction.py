"""Shared transaction boundary for stock-changing services.

Branch then inventory rows (UUID order) is the lock order. Branch serialization
also protects creation of an inventory row that does not exist yet. Inventory
row locks coordinate with legacy offline writers until their full phase-4 audit.
"""
from functools import wraps

from fastapi import HTTPException

from app.models.branch import Branch
from app.models.inventory import Inventory


def commercial_transaction(function):
    @wraps(function)
    def execute(db, *args, **kwargs):
        try:
            return function(db, *args, **kwargs)
        except Exception:
            db.rollback()
            raise
    return execute


def lock_branch(db, branch_id):
    branch = (db.query(Branch).filter(Branch.id == branch_id)
              .populate_existing().with_for_update(of=Branch).first())
    if not branch:
        raise HTTPException(404, 'Sucursal no encontrada')
    # Refresh values after waiting; an identity-map hit can otherwise be stale.
    (db.query(Inventory).filter(Inventory.sucursal_id == branch_id)
       .order_by(Inventory.id).populate_existing().with_for_update(of=Inventory).all())
    return branch
