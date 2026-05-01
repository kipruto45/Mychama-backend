from rest_framework.routers import DefaultRouter


def register_routes(router: DefaultRouter | None = None) -> DefaultRouter:
    """Register API viewsets here as modules are implemented."""
    api_router = router or DefaultRouter()
    return api_router


v1_router = register_routes()
