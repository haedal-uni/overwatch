import time

from django.core.cache import cache

CHAT_ACTIVITY_CACHE_KEY = "ow:chat:last_request"


class ChatActivityMiddleware:
    """챗봇 API 요청 시각을 캐시에 남긴다(다른 앱이 챗봇이 쉬는지 알 수 있게, 앱끼리 서로 참조하지 않는다)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.startswith("/chat/api/"):
            cache.set(CHAT_ACTIVITY_CACHE_KEY, time.time(), 600)
        return self.get_response(request)
