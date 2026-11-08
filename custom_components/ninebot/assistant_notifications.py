"""Native generic notices after durable task transitions; never task authority."""

import hashlib
from collections.abc import Mapping
from datetime import datetime

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .assistant_approval import APPROVAL_TTL, ApprovalTicket

TEXT = {
    "en": {
        "review": "A history task is ready for review.",
        "completed": "A history task has finished.",
        "attention": "A history task needs attention.",
        "open": "Sign in as the same Home Assistant user that requested it and open "
        "[Ninebot Assistant](/ninebot-assistant) to review the current task state.",
    },
    "zh": {
        "review": "有历史任务等待审核。",
        "completed": "有历史任务已结束。",
        "attention": "有历史任务需要处理。",
        "open": "请使用申请任务时的同一个 Home Assistant 用户登录，打开 "
        "[Ninebot Assistant](/ninebot-assistant) 查看当前任务状态。",
    },
}


def notification_id(entry_id: str) -> str:
    return "ninebot_assistant_" + hashlib.sha256(entry_id.encode()).hexdigest()


def _phase(ticket: ApprovalTicket) -> tuple[datetime | None, str | None]:
    return ticket.approved_at, ticket.progress.state if ticket.progress else None


def notice_kind(
    previous: Mapping[str, ApprovalTicket], records: Mapping[str, ApprovalTicket], now: datetime
) -> str | None:
    changed = [
        row
        for key, row in records.items()
        if key not in previous
        or previous[key].approved_at != row.approved_at
        or (row.terminal and _phase(previous[key]) != _phase(row))
    ]
    if not changed:
        return None
    if any(
        not row.terminal
        and row.approved_at is None
        and row.plan.created_at <= now < row.plan.created_at + APPROVAL_TTL
        for row in records.values()
    ):
        return "review"
    terminal = [row.progress.state for row in changed if row.terminal and row.progress]
    if "failed" in terminal or "partial" in terminal:
        return "attention"
    if "completed" in terminal:
        return "completed"
    if "cancelled" in terminal or any(
        row.approved_at is not None
        for row in changed
        if row.task_id not in previous or previous[row.task_id].approved_at != row.approved_at
    ):
        return "dismiss"
    return None


@callback
def async_publish(hass: HomeAssistant, entry_id: str, kind: str) -> None:
    if kind == "dismiss":
        async_dismiss(hass, entry_id)
        return
    if kind not in {"review", "completed", "attention"}:
        return
    text = TEXT["zh" if hass.config.language.startswith("zh") else "en"]
    persistent_notification.async_create(
        hass,
        f"{text[kind]}\n\n{text['open']}",
        title="Ninebot Assistant",
        notification_id=notification_id(entry_id),
    )


@callback
def async_dismiss(hass: HomeAssistant, entry_id: str) -> None:
    persistent_notification.async_dismiss(hass, notification_id(entry_id))
