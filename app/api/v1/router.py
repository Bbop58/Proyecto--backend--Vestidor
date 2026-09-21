from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.api.v1.roles import router as roles_router
from app.api.v1.permissions import router as permissions_router
from app.api.v1.branches import router as branches_router
from app.api.v1.categories import router as categories_router
from app.api.v1.products import router as products_router
from app.api.v1.variants import router as variants_router
from app.api.v1.suppliers import router as suppliers_router
from app.api.v1.seasons import router as seasons_router
from app.api.v1.inventory import router as inventory_router
from app.api.v1.reservations import router as reservations_router
from app.api.v1.sales import router as sales_router
from app.api.v1.payments import router as payments_router
from app.api.v1.ai import router as ai_router
from app.api.v1.voice_reports import router as voice_reports_router
from app.api.v1.notifications import router as notifications_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(users_router)
api_v1_router.include_router(roles_router)
api_v1_router.include_router(permissions_router)
api_v1_router.include_router(branches_router)
api_v1_router.include_router(categories_router)
api_v1_router.include_router(products_router)
api_v1_router.include_router(variants_router)
api_v1_router.include_router(suppliers_router)
api_v1_router.include_router(seasons_router)
api_v1_router.include_router(inventory_router)
api_v1_router.include_router(reservations_router)
api_v1_router.include_router(sales_router)
api_v1_router.include_router(payments_router)
api_v1_router.include_router(ai_router)
api_v1_router.include_router(voice_reports_router)
api_v1_router.include_router(notifications_router)





