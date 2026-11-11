"""Agent tool-call safety policy.

This module is intentionally independent from the LLM and LangGraph runtime so
the same rules can be unit-tested and reused by other entry points.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

from config import (
    SAFE_CREATE_THRESHOLD,
    SAFE_DELETE_THRESHOLD,
    SAFE_UPDATE_THRESHOLD,
)


from core.tool_catalog import TOOL_POLICIES


@dataclass(frozen=True)
class MutationCounts:
    creates: int = 0
    updates: int = 0
    deletes: int = 0

    def __add__(self, other: "MutationCounts") -> "MutationCounts":
        return MutationCounts(
            creates=self.creates + other.creates,
            updates=self.updates + other.updates,
            deletes=self.deletes + other.deletes,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "creates": self.creates,
            "updates": self.updates,
            "deletes": self.deletes,
        }


@dataclass(frozen=True)
class SafetyDecision:
    requires_approval: bool
    categories: tuple[str, ...]
    totals: MutationCounts


def classify_tool(name: str) -> str | None:
    """Return the mutation category for a tool, or ``None`` if read-only."""
    policy = TOOL_POLICIES.get(name)
    if policy is None:
        return "unknown"
    return None if policy.effect == "read" else policy.effect


def confirmation_policy(tool_call):
    """Resolve risk from the saved immutable plan, never caller-supplied flags."""
    function = tool_call.get('function') or {}
    name = function.get('name') or ''
    policy = TOOL_POLICIES.get(name)
    if policy is None:
        return 'unknown', True
    return (None if policy.effect == 'read' else policy.effect), policy.always_confirm


def _argument_item_count(arguments: str) -> int:
    """Estimate how many records a single batch-shaped tool call affects."""
    try:
        payload = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        return 1

    if not isinstance(payload, dict):
        return 1

    for key in ("items", "records", "entities"):
        value = payload.get(key)
        if isinstance(value, list):
            return max(1, len(value))

    count = payload.get("count")
    if isinstance(count, int) and count > 0:
        return count
    return 1


def count_tool_calls(tool_calls: Iterable[dict]) -> MutationCounts:
    creates = updates = deletes = 0
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        category, _ = confirmation_policy(tool_call)
        amount = _argument_item_count(str(function.get("arguments") or "{}"))
        if category == "create":
            creates += amount
        elif category == "update":
            updates += amount
        elif category == "delete":
            deletes += amount
    return MutationCounts(creates, updates, deletes)


def evaluate_mutations(
    completed: MutationCounts,
    pending_tool_calls: Iterable[dict],
    intended: MutationCounts | None = None,
    require_review: bool = False,
    auto_confirm: bool = False,
) -> SafetyDecision:
    pending_tool_calls = list(pending_tool_calls)
    pending = count_tool_calls(pending_tool_calls)
    totals = completed + pending
    if intended is not None:
        totals = MutationCounts(
            creates=max(totals.creates, intended.creates),
            updates=max(totals.updates, intended.updates),
            deletes=max(totals.deletes, intended.deletes),
        )
    categories: list[str] = []
    # Historical updates do not re-confirm each subsequent ordinary write.
    # Keep the cumulative creation cap so splitting a large creation into
    # single calls cannot silently bypass its existing size limit.
    scope = pending if intended is None else MutationCounts(
        creates=max(pending.creates, intended.creates),
        updates=max(pending.updates, intended.updates),
        deletes=max(pending.deletes, intended.deletes))
    if pending.creates and totals.creates > SAFE_CREATE_THRESHOLD:
        categories.append("创建")
    if pending.updates and scope.updates > SAFE_UPDATE_THRESHOLD:
        categories.append("修改")
    if pending.deletes and scope.deletes > SAFE_DELETE_THRESHOLD:
        categories.append("删除")
    if any(confirmation_policy(call)[1] for call in pending_tool_calls):
        categories.append("需核对业务影响的操作")
    if require_review and any(confirmation_policy(call)[0] is not None for call in pending_tool_calls):
        categories.append('用户要求执行前确认')
    if auto_confirm and not require_review and all(
        (call.get('function') or {}).get('name') in TOOL_POLICIES for call in pending_tool_calls
    ):
        categories = []
    if any((policy := TOOL_POLICIES.get((call.get('function') or {}).get('name','')))
           and policy.manual_confirm_only for call in pending_tool_calls):
        categories.append('必须人工确认的旧请求处置')
    return SafetyDecision(bool(categories), tuple(categories), totals)


def requests_execution_review(message: str) -> bool:
    """An explicit request to review before writing overrides routine exemptions."""
    return bool(re.search(
        r'(?:先|提交前|执行前|创建前).{0,24}(?:确认|审核|审阅)|'
        r'等我确认|经我确认|确认后再|确认后才能|确认后执行|确认后提交', message or ''))


def infer_intended_mutations(
    message: str,
    pending_tool_calls: Iterable[dict],
) -> MutationCounts:
    """Use explicit tool arguments; product-specific count inference belongs in adapters."""
    return count_tool_calls(pending_tool_calls)


def is_explicit_confirmation(text: str) -> bool:
    """Accept an explicit approval only; substrings such as “不是” never pass."""
    normalized = re.sub(r"[\s，。！？、,.!?;；:：]+", "", (text or "").casefold())
    if not normalized:
        return False

    negative_markers = ("不", "否", "取消", "停止", "拒绝", "no", "cancel", "stop")
    if any(marker in normalized for marker in negative_markers):
        return False

    exact = {
        "确认",
        "确定",
        "同意",
        "批准",
        "继续",
        "继续执行",
        "确认继续",
        "确认执行",
        "确认创建",
        "确认修改",
        "确认删除",
        "确认提交",
        "确认核实",
        "确认采集",
        "确认投料",
        "好的确认",
        "好的确认执行",
        "ok",
        "okay",
        "yes",
        "y",
    }
    return normalized in exact


def is_explicit_rejection(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？、,.!?;；:：]+", "", (text or "").casefold())
    if not normalized:
        return False
    return normalized in {
        "不",
        "否",
        "不确认",
        "取消",
        "取消执行",
        "停止",
        "停止执行",
        "拒绝",
        "no",
        "n",
        "cancel",
        "stop",
    }


def approval_prompt(decision: SafetyDecision) -> str:
    return (
        "以下操作需要你确认，目前尚未提交这些操作。\n"
        "核对无误后点击“确认执行”，或回复“确认执行”；如需调整，请取消后说明修改内容。"
    )


def plan_fingerprint(calls):
    """Bind approval to ordered tool names and canonical arguments."""
    import hashlib
    plan = []
    for call in calls:
        function = call.get("function") or {}
        args = function.get("arguments") or "{}"
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            pass
        plan.append({"tool": function.get("name"), "arguments": args})
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan_preview(calls):
    lines = []
    for index, call in enumerate(calls, 1):
        function = call.get("function") or {}
        arguments = str(function.get("arguments") or "{}")
        lines.append(f"{index}. {function.get('name')}: {arguments}")
    return "待执行工具与参数：\n" + "\n".join(lines)
