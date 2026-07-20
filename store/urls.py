from django.urls import path
from . import views

urlpatterns = [
    path("", views.product_list, name="product_list"),
    path("add/", views.product_create, name="product_create"),
    path("edit/<int:pk>/", views.product_update, name="product_update"),
    path("delete/<int:pk>/", views.product_delete, name="product_delete"),
    path("upload-db/", views.database_upload, name="database_upload"),
    path("export-db/", views.database_export, name="database_export"),
    path("analytics/", views.analytics_page, name="analytics"),
    path("discount/", views.apply_discount, name="apply_discount"),
]
