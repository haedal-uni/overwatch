from django.utils import timezone
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

    # 브라우저가 만든 챗봇 기기 ID와 같은 브라우저의 게임 기기 ID(없으면 빈 값). 한 사람이 언제 얼마나 썼는지 묶어 보는 데 쓴다.
    device_id = models.CharField("기기", max_length=64, blank=True, default="", db_index=True)
    game_device_id = models.CharField("게임 기기", max_length=64, blank=True, default="", db_index=True)

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


class UnknownScoreboardManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(role="AI", intent="scoreboard_analysis", metadata__has_unknown=True)


class UnknownScoreboardLog(ChatLog):
    """영웅을 못 알아본 줄이 있는 스탯창 분석만 모아 보여주는 프록시 모델(관리자 확인용)."""

    objects = UnknownScoreboardManager()

    class Meta:
        proxy = True
        verbose_name = "영웅 미확인 스탯창"
        verbose_name_plural = "영웅 미확인 스탯창"


class ChatOpinion(models.Model):
    """챗봇 화면의 "의견 보내기"로 받은 글(답변 불만족과 별개로, 챗봇 전반에 대한 의견)."""

    message = models.TextField("의견", max_length=1000)
    device_id = models.CharField("게임 기기 ID", max_length=64, blank=True, default="")
    user_agent = models.CharField("브라우저", max_length=200, blank=True, default="")
    is_resolved = models.BooleanField("반영함", default=False, db_index=True)
    resolution_note = models.TextField("처리 메모", blank=True, default="")
    created_at = models.DateTimeField("받은 시각", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "챗봇 의견"
        verbose_name_plural = "챗봇 의견"

    def __str__(self):
        return self.message[:30]


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

    POSITION_CHOICES = [("popup", "팝업(확인 누르면 닫힘, 한 번 본 사람은 안 뜸)"), ("top", "위쪽"), ("bottom", "아래쪽")]

    message = models.TextField("공지 내용", max_length=500)
    position = models.CharField("보이는 곳", max_length=10, choices=POSITION_CHOICES, default="popup")
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


class BatchJob(models.Model):
    """스탯창 일괄 올리기 한 번. id가 곧 조회 열쇠라 추측할 수 없는 값(uuid)이다."""

    id = models.UUIDField("작업", primary_key=True, editable=False)
    result = models.CharField("승패", max_length=8, choices=MatchStat.RESULT_CHOICES)
    created_at = models.DateTimeField("올린 시각", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "일괄 올리기"
        verbose_name_plural = "일괄 올리기"

    def __str__(self):
        return f"일괄 {str(self.id)[:8]}"


class BatchImage(models.Model):
    """일괄 올리기의 이미지 한 장. 분석이 끝나면 임시 파일은 지운다(영웅 못 알아본 판의 원본은 분석이 따로 남긴다)."""

    STATUS_CHOICES = [("pending", "대기"), ("processing", "분석 중"), ("done", "완료"), ("failed", "실패")]

    job = models.ForeignKey(BatchJob, on_delete=models.CASCADE, related_name="images")
    idx = models.PositiveSmallIntegerField("순번")
    status = models.CharField("상태", max_length=12, choices=STATUS_CHOICES, default="pending", db_index=True)
    path = models.CharField("임시 파일", max_length=300, blank=True, default="")
    mime_type = models.CharField("형식", max_length=40, default="image/png")
    attempts = models.PositiveSmallIntegerField("시도", default=0)
    turn_id = models.CharField("분석 턴", max_length=64, blank=True, default="")
    match = models.JSONField("판 요약", null=True, blank=True)
    error = models.CharField("실패 이유", max_length=200, blank=True, default="")
    updated_at = models.DateTimeField("바뀐 시각", auto_now=True)

    class Meta:
        ordering = ["job_id", "idx"]
        unique_together = [("job", "idx")]
        verbose_name = "일괄 올리기 이미지"
        verbose_name_plural = "일괄 올리기 이미지"


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


class AdminSeen(models.Model):
    """관리자가 상세 화면을 한 번이라도 연 의견·로그(미확인과 미반영을 구분한다)."""

    kind = models.CharField("종류", max_length=40)
    obj_id = models.PositiveIntegerField("번호")
    seen_at = models.DateTimeField("확인한 시각", auto_now_add=True)
    todo = models.BooleanField("반영 예정", default=False, help_text="열어 보고 나중에 반영하려고 표시한 것. 표시하지 않은 확인한 것은 개수에 세지 않는다.")

    class Meta:
        verbose_name = "확인 기록"
        verbose_name_plural = "확인 기록"
        constraints = [models.UniqueConstraint(fields=["kind", "obj_id"], name="admin_seen_unique")]


class PatchNote(models.Model):
    """홈 화면 오른쪽 위 "패치 노트"에 쌓이는 업데이트 글."""

    title = models.CharField("제목", max_length=80)
    body = models.TextField("내용", help_text="줄바꿈 그대로 보인다. 길게 써도 된다.")
    is_published = models.BooleanField("공개", default=True)
    published_at = models.DateTimeField("올린 날", default=timezone.now)

    class Meta:
        verbose_name = "패치 노트"
        verbose_name_plural = "패치 노트"
        ordering = ["-published_at", "-id"]

    def __str__(self):
        return self.title
