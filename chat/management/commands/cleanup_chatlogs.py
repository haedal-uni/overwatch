"""오래된 대화 로그와 그에 딸린 스탯창 디버그 이미지를 정리한다.

    python manage.py cleanup_chatlogs --days 90            # 90일 지난 로그 삭제
    python manage.py cleanup_chatlogs --days 90 --dry-run  # 삭제 없이 건수만 확인

ChatLog.metadata에는 턴마다 context_before/context_after와 카드 전문이 통째로
들어가고, 스탯창 분석은 logs/scoreboard_debug/{turn_id}/에 행별 crop 이미지를
남긴다. 지금까지는 관리자가 화면에서 수동으로 지우는 방법밖에 없어서 둘 다
무한정 쌓였다 — 보존 기간을 정해두고 주기적으로(예: cron/스케줄러) 돌리면
된다.

DB 로그가 이미 지워진 뒤 디스크 폴더만 남은 "고아 폴더"도 함께 정리한다
(과거 삭제가 권한 문제로 실패했던 경우 등).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from chat.vision.debug_images import delete_scoreboard_debug_dirs, list_debug_turn_ids
from chat.models import ChatLog


class Command(BaseCommand):
    help = "지정한 일수보다 오래된 ChatLog와 스탯창 디버그 이미지 폴더를 삭제한다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=90,
            help="보존 기간(일). 이보다 오래된 로그를 삭제한다. 기본값 90.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="실제로 지우지 않고 삭제 대상 건수만 출력한다.",
        )
        parser.add_argument(
            "--keep-orphan-dirs", action="store_true",
            help="DB 로그가 없는 고아 디버그 폴더는 그대로 둔다.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]

        if days < 1:
            raise CommandError("--days는 1 이상이어야 합니다.")

        # USE_TZ=False라 양쪽 다 naive datetime이다(True로 바꿔도 그대로 동작).
        cutoff = timezone.now() - timedelta(days=days)

        old_logs = ChatLog.objects.filter(created_at__lt=cutoff)
        target_count = old_logs.count()
        turn_ids = list(old_logs.values_list("turn_id", flat=True).distinct())

        orphan_turn_ids = []
        if not options["keep_orphan_dirs"]:
            disk_turn_ids = set(list_debug_turn_ids())
            if disk_turn_ids:
                known = set(
                    ChatLog.objects.filter(turn_id__in=disk_turn_ids)
                    .values_list("turn_id", flat=True)
                )
                orphan_turn_ids = sorted(disk_turn_ids - known)

        if dry_run:
            self.stdout.write(
                f"[dry-run] {cutoff:%Y-%m-%d %H:%M} 이전 로그 {target_count}건, "
                f"디버그 폴더 후보 {len(turn_ids)}개, 고아 폴더 {len(orphan_turn_ids)}개"
            )
            return

        deleted_count, _ = old_logs.delete()
        failed = delete_scoreboard_debug_dirs(turn_ids + orphan_turn_ids)

        self.stdout.write(
            f"로그 {deleted_count}건을 삭제했습니다"
            f"(고아 디버그 폴더 {len(orphan_turn_ids)}개 포함 정리)."
        )
        if failed:
            self.stderr.write(
                f"디버그 폴더 {len(failed)}개는 권한 문제로 삭제하지 못했습니다: "
                f"{', '.join(failed[:10])}"
            )
