from django.db import models


class ChatLog(models.Model):
    ROLE_CHOICES = [
        ("USER", "사용자"),
        ("AI", "AI"),
        ("ERROR", "오류"),
    ]

    log_session_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    turn_id = models.CharField(max_length=100, db_index=True)

    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    message = models.TextField()

    intent = models.CharField(max_length=50, null=True, blank=True)
    current_hero = models.CharField(max_length=50, null=True, blank=True)
    target_enemy = models.CharField(max_length=50, null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    # 사용자가 AI 답변에 만족하지 못해 남긴 피드백. AI 답변 로그(role="AI")에만 채워진다.
    is_unsatisfied = models.BooleanField("불만족 여부", default=False, db_index=True)
    feedback_reason = models.TextField("불만족 사유", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"[{self.role}] {self.message[:30]}"


class ErrorLogManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(role="ERROR")


class ErrorChatLog(ChatLog):
    """
    ChatLog 중 role="ERROR"인 것만 모아 보여주는 프록시 모델.
    실제 테이블은 chat_chatlog를 그대로 쓰고, 관리자 페이지 사이드바에
    "오류 로그"로 별도 진입점을 만들기 위한 용도다.
    """

    objects = ErrorLogManager()

    class Meta:
        proxy = True
        verbose_name = "오류 로그"
        verbose_name_plural = "오류 로그"


class UnsatisfiedAnswerManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(role="AI", is_unsatisfied=True)


class UnsatisfiedChatLog(ChatLog):
    """
    사용자가 "만족스럽지 않다"고 표시한 AI 답변만 모아 보여주는 프록시 모델.
    """

    objects = UnsatisfiedAnswerManager()

    class Meta:
        proxy = True
        verbose_name = "불만족 답변"
        verbose_name_plural = "불만족 답변"