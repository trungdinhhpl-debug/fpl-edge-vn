from fastapi import APIRouter

from app.api.routes import catalog, health, optimizer_routes

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(optimizer_routes.router, tags=["optimizer"])
