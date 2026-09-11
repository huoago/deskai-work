from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(tags=["recovery"])

VISIBLE_STATUSES = {
    "applied",
    "rolling_back",
    "rolled_back",
    "recycled",
    "restoring",
    "restored",
    "recovery_required",
}

DIAGNOSTIC_TITLES = {
    "partial_transaction": "事务只恢复了一部分",
    "ambiguous_state": "磁盘状态存在歧义",
    "hash_mismatch": "文件哈希与事务记录不一致",
    "target_conflict": "目标路径已被占用",
    "quarantine_issue": "回收隔离副本异常",
    "backup_issue": "自动备份不可用或不一致",
    "write_access": "Workspace 写权限或文件权限异常",
    "file_lock": "文件仍被其他程序占用",
    "metadata_commit": "磁盘变更与数据库最终状态未能同步提交",
    "startup_recovery_failed": "启动恢复未能确定安全状态",
    "manual_review": "需要人工核对实际磁盘状态",
}

DIAGNOSTIC_SUMMARIES = {
    "partial_transaction": "DeskAI 检测到批次或恢复流程未能完整回到一个一致状态，继续自动写入可能扩大不一致范围。",
    "ambiguous_state": "现有路径、副本或事务成员无法唯一映射到原始态/应用态/回收态，因此自动恢复已经冻结。",
    "hash_mismatch": "磁盘文件内容已经不同于事务记录中的受保护 SHA-256，DeskAI 不会覆盖或忽略该变化。",
    "target_conflict": "恢复或回滚需要使用的目标路径已经存在其他内容；no-overwrite 规则阻止继续操作。",
    "quarantine_issue": "回收隔离副本缺失、不可验证或与记录不一致，不能据此自动重建 Workspace 原件。",
    "backup_issue": "自动备份缺失、不可访问或未通过完整性校验，不能安全执行自动回滚。",
    "write_access": "当前 Workspace/文件系统不满足原事务的写入权限门禁，DeskAI 已停止修改磁盘。",
    "file_lock": "Office 或其他程序可能仍持有文件锁，原事务无法安全替换、移动或恢复文件。",
    "metadata_commit": "文件系统操作与数据库最终状态之间出现提交失败，需要先确认磁盘实际状态再决定人工恢复路径。",
    "startup_recovery_failed": "DeskAI 在启动时发现中断事务，但无法自动证明一个唯一且安全的恢复结果。",
    "manual_review": "持久化事务记录表明自动恢复不再安全，但现有错误信息不足以给出更具体的分类。",
}


def _updated_at(item: dict[str, Any]) -> str:
    for key in (
        "restored_at",
        "rolled_back_at",
        "recycled_at",
        "applied_at",
        "confirmed_at",
        "created_at",
    ):
        value = item.get(key)
        if value:
            return str(value)
    return str(item.get("created_at") or "")


def _is_recovery_required(item: dict[str, Any]) -> bool:
    return bool(
        item.get("recovery_required")
        or item.get("status") == "recovery_required"
    )


def _action(kind: str, item: dict[str, Any]) -> str | None:
    if _is_recovery_required(item):
        return None
    if kind in {
        "source_edit",
        "source_edit_batch",
        "file_organization",
        "file_organization_batch",
    } and item.get("can_rollback"):
        return "rollback"
    if kind in {"file_recycle", "file_recycle_batch"} and item.get("can_restore"):
        return "restore"
    return None


def _filenames(kind: str, item: dict[str, Any]) -> list[str]:
    if kind == "source_edit":
        return [str(item.get("filename") or "Unknown file")]
    if kind == "source_edit_batch":
        return [
            str(member.get("filename") or "Unknown file")
            for member in item.get("edits") or []
        ]
    if kind == "file_organization":
        return [str(item.get("filename") or "Unknown file")]
    if kind == "file_organization_batch":
        return [
            str(member.get("filename") or "Unknown file")
            for member in item.get("operations") or []
        ]
    if kind == "file_recycle":
        return [str(item.get("filename") or "Unknown file")]
    return [
        str(member.get("filename") or "Unknown file")
        for member in item.get("items") or []
    ]


def _paths(kind: str, item: dict[str, Any]) -> list[str]:
    if kind == "file_organization":
        return [
            value
            for value in (
                item.get("original_path"),
                item.get("target_path"),
            )
            if value
        ]
    if kind == "file_organization_batch":
        result: list[str] = []
        for member in item.get("operations") or []:
            for key in ("original_path", "target_path"):
                value = member.get(key)
                if value:
                    result.append(str(value))
        return result
    if kind == "file_recycle":
        value = item.get("original_path")
        return [str(value)] if value else []
    if kind == "file_recycle_batch":
        return [
            str(member["original_path"])
            for member in item.get("items") or []
            if member.get("original_path")
        ]
    return []


def _diagnostic_code(message: str) -> tuple[str, str]:
    normalized = message.lower()
    if any(
        marker in normalized
        for marker in (
            "rollback was incomplete",
            "restoration was incomplete",
            "reapply was incomplete",
            "could not restore every",
            "could not return every",
            "automatic rollback was incomplete",
            "automatic source restoration was incomplete",
        )
    ):
        return "partial_transaction", "high"
    if "ambiguous" in normalized or "invalid state" in normalized:
        return "ambiguous_state", "high"
    if "hash" in normalized or "sha-256" in normalized or "sha256" in normalized:
        return "hash_mismatch", "high"
    if (
        "target" in normalized
        and any(marker in normalized for marker in ("exist", "occupied", "not empty"))
    ):
        return "target_conflict", "high"
    if "quarantine" in normalized:
        return "quarantine_issue", "high"
    if "backup" in normalized:
        return "backup_issue", "high"
    if any(
        marker in normalized
        for marker in ("write access", "write permission", "not writable", "write_allowed")
    ):
        return "write_access", "high"
    if "lock" in normalized or "in use" in normalized:
        return "file_lock", "high"
    if "database finalization" in normalized:
        return "metadata_commit", "high"
    if "startup" in normalized or "automatic recovery" in normalized:
        return "startup_recovery_failed", "medium"
    return "manual_review", "low"


def _diagnostic(kind: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_recovery_required(item):
        return None

    message = str(item.get("error_message") or "").strip()
    code, confidence = _diagnostic_code(message)

    evidence = [
        f"entity_type={kind}",
        f"status={item.get('status') or 'unknown'}",
    ]
    if message:
        evidence.append(f"persisted_error={message}")

    checks = [
        "打开对应 Task 与审计记录，确认最后一个成功完成的事务阶段。",
        "在任何人工修改前，记录当前所有相关路径是否存在、文件大小和 SHA-256。",
    ]
    if kind.startswith("source_edit"):
        checks.append(
            "核对 Workspace 原文件、自动备份以及已应用/候选内容的 SHA-256，确认哪一份对应事务记录。"
        )
    elif kind.startswith("file_organization"):
        checks.append(
            "同时核对 original_path 与 target_path，确认两端是否存在、各自 SHA-256，以及目标路径是否由外部程序创建。"
        )
    else:
        checks.append(
            "同时核对 Workspace 原路径与私有隔离副本，确认隔离副本存在且 SHA-256 与原始记录一致。"
        )

    code_specific = {
        "partial_transaction": "逐个建立批次成员现状清单，先确定整个批次应统一回到原始态、应用态或回收态，再制定人工恢复顺序。",
        "ambiguous_state": "保留所有现有副本，不移动、不覆盖，先把每个成员标记为 original/applied/recycled/unknown 后再处理。",
        "hash_mismatch": "计算现有文件 SHA-256，并与事务记录中的原始/应用哈希逐项比对；把无法匹配的文件视为外部新版本。",
        "target_conflict": "确认占用目标路径的文件来源；将其作为独立用户数据保留，禁止为了恢复事务而直接覆盖。",
        "quarantine_issue": "验证隔离副本的存在性、大小和 SHA-256；在隔离副本未通过验证前不要重建原路径。",
        "backup_issue": "验证自动备份是否存在、可读且哈希正确；备份不可验证时不要执行内容回滚。",
        "write_access": "先恢复 Workspace root 的 write_allowed 与底层文件系统写权限，再重新评估磁盘状态；不要绕过权限门禁。",
        "file_lock": "关闭可能占用文件的 Office/编辑器/同步程序，再重新记录路径与 SHA-256 状态。",
        "metadata_commit": "保持磁盘内容不再变化，先对照事务数据库记录与实际路径状态，确认文件系统动作是否已经成功完成。",
        "startup_recovery_failed": "对照异常退出前后的审计时间线，确认启动恢复尝试之前磁盘处于哪个一致状态。",
        "manual_review": "保留所有原件、备份与隔离副本，依据 Task/审计时间线人工确定唯一可信版本。",
    }
    checks.append(code_specific[code])

    prohibited = [
        "不要 force overwrite 或覆盖任何已存在目标文件。",
        "不要忽略、伪造或手工改写 SHA-256 校验结果。",
        "不要删除自动备份或回收隔离副本。",
    ]
    if item.get("transactional"):
        prohibited.append("不要只修复单个批次成员后继续原批次；必须按整个事务核对一致性。")

    return {
        "code": code,
        "severity": "blocked",
        "confidence": confidence,
        "title": DIAGNOSTIC_TITLES[code],
        "summary": DIAGNOSTIC_SUMMARIES[code],
        "evidence": evidence,
        "guided_checks": checks,
        "prohibited_actions": prohibited,
        "automatic_repair_available": False,
    }


def _entry(
    kind: str,
    scope: str,
    item: dict[str, Any],
    count_key: str | None = None,
) -> dict[str, Any]:
    filenames = _filenames(kind, item)
    count = int(item.get(count_key) or len(filenames) or 1) if count_key else 1
    return {
        "id": item["id"],
        "entity_type": kind,
        "scope": scope,
        "task_id": item.get("task_id"),
        "workspace_id": item.get("workspace_id"),
        "status": item.get("status"),
        "summary": item.get("summary") or "",
        "created_at": item.get("created_at"),
        "updated_at": _updated_at(item),
        "error_message": item.get("error_message"),
        "item_count": count,
        "filenames": filenames,
        "paths": _paths(kind, item),
        "action": _action(kind, item),
        "recovery_required": _is_recovery_required(item),
        "transactional": bool(item.get("transactional")),
        "diagnostic": _diagnostic(kind, item),
    }


@router.get("/recovery")
def list_recovery_entries(
    request: Request,
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for item in request.app.state.source_edit_service.list(
        workspace_id=workspace_id,
        limit=500,
    ):
        if item.get("batch_id") or item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(_entry("source_edit", "single", item))

    for item in request.app.state.source_edit_batch_service.list(
        workspace_id=workspace_id,
        limit=200,
    ):
        if item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(
            _entry("source_edit_batch", "batch", item, "edit_count")
        )

    for item in request.app.state.file_organization_service.list(
        workspace_id=workspace_id,
        limit=500,
    ):
        if item.get("batch_id") or item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(_entry("file_organization", "single", item))

    for item in request.app.state.file_organization_batch_service.list(
        workspace_id=workspace_id,
        limit=200,
    ):
        if item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(
            _entry(
                "file_organization_batch",
                "batch",
                item,
                "operation_count",
            )
        )

    for item in request.app.state.file_recycle_service.list(
        workspace_id=workspace_id,
        limit=500,
    ):
        if item.get("batch_id") or item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(_entry("file_recycle", "single", item))

    for item in request.app.state.file_recycle_batch_service.list(
        workspace_id=workspace_id,
        limit=200,
    ):
        if item.get("status") not in VISIBLE_STATUSES:
            continue
        candidates.append(
            _entry("file_recycle_batch", "batch", item, "item_count")
        )

    candidates.sort(
        key=lambda item: (str(item.get("updated_at") or ""), item["id"]),
        reverse=True,
    )
    return candidates[:limit]
