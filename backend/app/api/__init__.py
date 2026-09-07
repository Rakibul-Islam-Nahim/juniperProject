"""API package."""
from app.api.health import router as health_router
from app.api.labs import router as labs_router
from app.api.ws import router as ws_router

__all__ = ["health_router", "labs_router", "ws_router"]