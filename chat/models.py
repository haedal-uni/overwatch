from django.db import models


class ChatLog(models.Model):
    ROLE_CHOICES = [
        ("USER", "사용자"),
        ("AI", "AI"),
        ("ERROR", "오류"),
    ]

    log_session_id = models.CharField("세션", max_length=100, null=True, blank=True, db_index=True)
    turn_id = models.CharField("턴", max_length=100, db_index=True)

    role = models.CharField("구분", max_length=10, choices=ROLE_CHOICES)
    message = models.TextField("메시지")

    intent = models.CharField("질문 의도", max_length=50, null=True, blank=True)
    current_hero = models.CharField("내 영웅", max_length=50, null=True, blank=True)
    target_enemy = models.CharField("상대 영웅", max_length=50, null=True, blank=True)

    metadata = models.JSONField("메타데이터", default=dict, blank=True)

    # 불만족 피드백(AI 답변 로그에만 채워진다).
    is_unsatisfied = models.BooleanField("불만족 여부", default=False, db_index=True)
    feedback_reason = models.TextField("불만족 사유", null=True, blank=True)

    # 관리자가 admin에서 직접 쓰는 처리 상태(완료 표시와 메모가 별개다).
    is_resolved = models.BooleanField("처리 완료", default=False, db_index=True)
    resolution_note = models.TextField("처리 메모", null=True, blank=True)

    created_at = models.DateTimeField("시각", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "채팅 로그"
        verbose_name_plural = "채팅 로그"
        # 기본 정렬이 없으면 페이지네이션 경고가 뜬다.
        ordering = ("-created_at",)
        indexes = [
            # 세션 조회가 이 조합으로 나간다.
            models.Index(
                fields=["log_session_id", "created_at"],
                name="chatlog_session_created_idx",
            ),
        ]

    def __str__(self):
        return f"[{self.role}] {self.message[:30]}"


class ErrorLogManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(role="ERROR")


class ErrorChatLog(ChatLog):
    """ChatLog 중 role="ERROR"인 것만 보여주는 프록시 모델(관리자 페이지 "오류 로그" 메뉴)."""

    objects = ErrorLogManager()

    class Meta:
        proxy = True
        verbose_name = "오류 로그"
        verbose_name_plural = "오류 로그"


class UnsatisfiedAnswerManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(role="AI", is_unsatisfied=True)


class UnsatisfiedChatLog(ChatLog):
    """사용자가 "만족스럽지 않다"고 표시한 AI 답변만 모아 보여주는 프록시 모델."""

    objects = UnsatisfiedAnswerManager()

    class Meta:
        proxy = True
        verbose_name = "불만족 답변"
        verbose_name_plural = "불만족 답변"


class SiteSetting(models.Model):
    """관리자 페이지에서 바꾸는 운영 설정(한 줄만 둔다)."""

    ROSTER_CHOICES = [(5, "5대5"), (6, "6대6")]

    meta_roster_size = models.PositiveSmallIntegerField(
        "현재 메타 인원수", choices=ROSTER_CHOICES, default=5,
        help_text="챗봇이 조합·역할을 판단할 때 기본으로 쓰는 규격. 사용자가 직접 5대5/6대6을 밝히면 그 값이 우선한다.",
    )
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        verbose_name = "챗봇 설정"
        verbose_name_plural = "챗봇 설정"

    def __str__(self):
        return "챗봇 설정"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class Notice(models.Model):
    """홈 화면(`/`)에 띄우는 공지사항."""

    POSITION_CHOICES = [("top", "위쪽"), ("bottom", "아래쪽")]

    message = models.TextField("공지 내용", max_length=500)
    position = models.CharField("보이는 곳", max_length=10, choices=POSITION_CHOICES, default="top")
    is_active = models.BooleanField("켜짐", default=True)
    starts_at = models.DateTimeField("시작", null=True, blank=True, help_text="비우면 바로 보인다.")
    ends_at = models.DateTimeField("끝", null=True, blank=True, help_text="비우면 끄기 전까지 보인다.")
    created_at = models.DateTimeField("만든 시각", auto_now_add=True)

    class Meta:
        verbose_name = "공지사항"
        verbose_name_plural = "공지사항"
        ordering = ["-created_at"]

    def __str__(self):
        return self.message[:40]



class MatchStat(models.Model):
    """스탯창 분석에서 읽은 판 수치(익명 통계용). 닉네임·이미지·세션·IP는 저장하지 않는다."""

    RESULT_CHOICES = [("win", "승리"), ("loss", "패배"), ("draw", "무승부")]

    created_at = models.DateTimeField("저장 시각", auto_now_add=True)
    stat_key = models.CharField("판 식별값", max_length=64, unique=True)
    ally = models.JSONField("우리팀 줄", default=list)
    enemy = models.JSONField("상대팀 줄", default=list)
    self_hero = models.CharField("내 영웅", max_length=40, blank=True, default="")
    self_rank = models.PositiveSmallIntegerField("내 순위", null=True, blank=True)
    result = models.CharField("승패", max_length=8, choices=RESULT_CHOICES, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "판 통계"
        verbose_name_plural = "판 통계"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.self_hero or '-'}"


class SyncGroup(models.Model):
    """PC·폰을 6자리 코드로 묶은 한 사람의 기록 묶음(로그인 없음). 3개월 동안 어느 기기도 오지 않으면 통째로 지운다."""

    # 내 기록(스탯창 판 요약, 최근 100판)과 저장한 대화(최대 2개, 마지막으로 바꾼 쪽이 이긴다).
    records = models.JSONField("내 기록", default=list)
    saved_chats = models.JSONField("저장한 대화", default=list)
    saved_chats_ts = models.BigIntegerField("저장한 대화 바꾼 시각(ms)", default=0)
    created_at = models.DateTimeField("만든 시각", auto_now_add=True)
    last_seen = models.DateTimeField("마지막 사용", auto_now=True, db_index=True)

    class Meta:
        verbose_name = "기기 연결"
        verbose_name_plural = "기기 연결"

    def __str__(self):
        return f"연결 {self.pk}"


class SyncDevice(models.Model):
    """연결 묶음에 속한 기기. device_id는 브라우저가 만든 무작위 값이다."""

    device_id = models.CharField("기기 ID", max_length=64, unique=True)
    group = models.ForeignKey(SyncGroup, on_delete=models.CASCADE, related_name="devices")
    label = models.CharField("기기 종류", max_length=40, blank=True)
    # 이 기기에서 센 이벤트 횟수({"chat", "cards", "shots": [판 해시]}). 묶음 합계는 기기별로 더한다.
    events = models.JSONField("이벤트 횟수", default=dict)
    last_seen = models.DateTimeField("마지막 사용", auto_now=True)

    class Meta:
        verbose_name = "연결된 기기"
        verbose_name_plural = "연결된 기기"

    def __str__(self):
        return f"{self.label or '기기'} {self.device_id[:8]}"


class SyncCode(models.Model):
    """다른 기기를 묶을 때 쓰는 6자리 숫자(10분, 한 번만)."""

    code = models.CharField("코드", max_length=6, db_index=True)
    group = models.ForeignKey(SyncGroup, on_delete=models.CASCADE, related_name="codes")
    expires_at = models.DateTimeField("만료")
    used = models.BooleanField("사용함", default=False)
