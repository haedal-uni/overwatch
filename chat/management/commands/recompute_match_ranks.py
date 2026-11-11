from django.core.management.base import BaseCommand

from chat.match_stats import recompute_rank
from chat.models import MatchStat


class Command(BaseCommand):
    help = "저장된 판 통계의 내 순위를 지금 종합 점수 비중으로 다시 계산해 갱신한다(영웅을 못 알아본 줄이 있는 판은 건너뜀)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="바뀔 판만 세고 저장하지 않는다")

    def handle(self, *args, **opts):
        changed = skipped = same = 0
        for stat in MatchStat.objects.all().iterator():
            rank = recompute_rank(stat)
            if rank is None:
                skipped += 1
            elif rank == stat.self_rank:
                same += 1
            else:
                changed += 1
                if not opts["dry_run"]:
                    MatchStat.objects.filter(pk=stat.pk).update(self_rank=rank)
        self.stdout.write(f"바뀜 {changed} · 같음 {same} · 건너뜀 {skipped}" + (" (저장 안 함)" if opts["dry_run"] else ""))
