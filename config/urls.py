"""URL configuration for the fuel-route-optimizer service."""

from django.urls import path
from django.views.generic import RedirectView

from api.api import api
from api.views import map_page

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="api-docs", permanent=False)),
    path("api/", api.urls),
    path("map/<str:request_id>", map_page, name="map-page"),
]
