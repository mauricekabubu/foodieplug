"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from .seo import robots_txt
from django.contrib.sitemaps.views import sitemap
from config.sitemaps import PublicPagesSitemap
from config.seo import robots_txt


sitemaps = {
    "public": PublicPagesSitemap,
}


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("foods/", include("apps.foods.urls")),
    path("", include("apps.shopping.urls")),
    path("pantry/", include("apps.pantry.urls")),
    path("budgets/", include("apps.budgets.urls")),
    path("meals/", include("apps.meals.urls")),
    path("ai/", include("apps.ai.urls")),
    path("insights/", include("apps.insights.urls")),
    path("accounts/", include("allauth.urls")),
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml",sitemap,{"sitemaps": sitemaps},name="django.contrib.sitemaps.views.sitemap"),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=str(settings.BASE_DIR / "static"),
    )
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )