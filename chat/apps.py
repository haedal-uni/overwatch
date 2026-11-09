import logging
import os

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

def _no_cache_static():
    """runserver가 내보내는 정적 파일에 Cache-Control: no-cache를 붙인다(폰이 옛 파일을 몇 시간씩 쓰지 않게, 바뀌지 않았으면 304)."""
    from django.contrib.staticfiles.handlers import StaticFilesHandlerMixin

    if getattr(StaticFilesHandlerMixin, "_ow_no_cache", False):
        return
    original = StaticFilesHandlerMixin.serve

    def serve(self, request):
        response = original(self, request)
        response["Cache-Control"] = "no-cache"
        return response

    StaticFilesHandlerMixin.serve = serve
    StaticFilesHandlerMixin._ow_no_cache = True


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"

    _initialized = False

    def ready(self):
        _no_cache_static()
        from chat.domain.intent_rules import set_meta_roster_provider
        from chat.site_settings import meta_roster_size
        set_meta_roster_provider(meta_roster_size)

        if "test" in os.sys.argv:
            return
        if settings.DEBUG and os.environ.get("RUN_MAIN") != "true":
            return
        if ChatConfig._initialized:
            return
        ChatConfig._initialized = True

        try:
            from chat.rag.components import initialize_chatbot
            initialize_chatbot()
            logger.info("ChatBot 초기화 완료")
        except Exception as exc:
            logger.exception("ChatBot 초기화 실패: %s", exc)