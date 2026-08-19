from fastapi import APIRouter

from app.api.v1 import auth, health_profile, ingredients, products, scan, scan_history

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(scan.router)
api_router.include_router(products.router)
api_router.include_router(ingredients.router)
api_router.include_router(health_profile.router)
api_router.include_router(scan_history.router)
