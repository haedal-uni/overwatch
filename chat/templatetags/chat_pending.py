from django import template

from chat.admin_pending import pending_counts

register = template.Library()


@register.simple_tag(takes_context=True)
def pending_map(context):
    """{changelist URL: 처리할 개수}"""
    request = context.get("request")
    return pending_counts(request) if request else {}


@register.filter
def lookup(mapping, key):
    return (mapping or {}).get(key, "")


@register.filter
def unseen(mapping, key):
    return ((mapping or {}).get(key) or {}).get("unseen", "")


@register.filter
def openn(mapping, key):
    return ((mapping or {}).get(key) or {}).get("open", "")
