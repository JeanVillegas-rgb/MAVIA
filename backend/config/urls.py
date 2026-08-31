from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("user.urls")),
    path("api/", include("lessons.urls")),
    path("api/", include("question_generation.urls")),
    path("api/course/", include("course.urls")),
    path("api/adaptive/", include("adaptive.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
