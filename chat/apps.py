import logging
import os

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"

    _initialized = False

    def ready(self):
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