from django import template

from chat.admin_dashboard import get_dashboard

register = template.Library()


@register.simple_tag
def chat_dashboard():
    """관리자 홈 챗봇 분석 요약."""
    return get_dashboard()
