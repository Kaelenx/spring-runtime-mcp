"""Normalize Flyway and Liquibase actuator beans without executing migrations."""

from __future__ import annotations

import re
from typing import Any

from ..models.queries import MigrationQuery

_FLYWAY = {
    "SUCCESS": "APPLIED",
    "FAILED": "FAILED",
    "PENDING": "PENDING",
    "OUT_OF_ORDER": "OUT_OF_ORDER",
    "MISSING_FAILED": "MISSING_FAILED",
    "MISSING_SUCCESS": "APPLIED",
    "FUTURE_SUCCESS": "APPLIED",
    "FUTURE_FAILED": "FAILED",
}
_LIQUIBASE = {
    "EXECUTED": "APPLIED",
    "FAILED": "FAILED",
    "MARK_RAN": "APPLIED",
    "RERAN": "APPLIED",
    "SKIPPED": "PENDING",
}
_ALGORITHM = re.compile(r"(\d+):")


def _checksum(value: Any) -> tuple[str | None, str]:
    if isinstance(value, bool) or value is None:
        return None, "absent"
    if isinstance(value, int):
        return str(value), "number"
    if isinstance(value, str) and value:
        return value[:128], "string"
    return None, "absent"


def _state(raw: str | None, table: dict[str, str]) -> tuple[str, str]:
    if raw is None:
        return "UNKNOWN", "unknown"
    mapped = table.get(raw)
    if mapped is None:
        return "UNKNOWN", "reported"
    return mapped, "reported"


def _keep(query: MigrationQuery, row: dict[str, Any]) -> bool:
    if query.context_id and row["contextId"] != query.context_id:
        return False
    if query.migration_bean and row["migrationBean"] != query.migration_bean:
        return False
    if query.states and row["state"] not in query.states:
        return False
    return not (query.version is not None and row["version"] != query.version)


def _from_flyway(body: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("contexts"), dict):
        raise ValueError("INVALID_MIGRATIONS_SHAPE")
    rows: list[dict[str, Any]] = []
    gaps: set[str] = set()
    for context_name in sorted(body["contexts"]):
        context = body["contexts"][context_name]
        if not isinstance(context, dict) or not isinstance(
            context.get("flywayBeans"), dict
        ):
            gaps.add("FLYWAY_BEANS_UNOBSERVED")
            continue
        parent = (
            context.get("parentId")
            if isinstance(context.get("parentId"), str)
            else None
        )
        for bean_name in sorted(context["flywayBeans"]):
            bean = context["flywayBeans"][bean_name]
            migrations = bean.get("migrations") if isinstance(bean, dict) else None
            if not isinstance(migrations, list):
                gaps.add("FLYWAY_MIGRATIONS_UNOBSERVED")
                continue
            for migration in migrations:
                if not isinstance(migration, dict):
                    gaps.add("MIGRATION_RECORD_UNREADABLE")
                    continue
                raw = (
                    migration.get("state")
                    if isinstance(migration.get("state"), str)
                    else None
                )
                state, evidence = _state(raw, _FLYWAY)
                version = (
                    migration.get("version")
                    if isinstance(migration.get("version"), str)
                    else None
                )
                script = (
                    migration.get("script")
                    if isinstance(migration.get("script"), str)
                    else None
                )
                checksum, checksum_type = _checksum(migration.get("checksum"))
                rows.append(
                    {
                        "endpointId": "flyway",
                        "contextId": context_name
                        if isinstance(context_name, str)
                        else None,
                        "parentContextId": parent,
                        "engine": "flyway",
                        "migrationBean": str(bean_name)[:256],
                        "identity": f"{bean_name}:{version or script or 'unversioned'}"[
                            :256
                        ],
                        "script": script[:256] if script else None,
                        "changeLog": None,
                        "changeSetId": None,
                        "author": None,
                        "version": version[:128] if version else None,
                        "description": None,
                        "checksum": checksum,
                        "checksumType": checksum_type,
                        "checksumAlgorithm": None,
                        "executionTimeMs": migration.get("executionTime")
                        if isinstance(migration.get("executionTime"), (int, float))
                        and not isinstance(migration.get("executionTime"), bool)
                        and migration["executionTime"] >= 0
                        else None,
                        "orderExecuted": migration.get("installedRank")
                        if type(migration.get("installedRank")) is int
                        and migration["installedRank"] >= 0
                        else None,
                        "executedAt": migration.get("installedOn")
                        if isinstance(migration.get("installedOn"), str)
                        else None,
                        "rawState": raw[:64] if raw else None,
                        "state": state,
                        "stateEvidence": evidence,
                        "attentionRequired": state
                        in {
                            "FAILED",
                            "PENDING",
                            "OUT_OF_ORDER",
                            "MISSING_FAILED",
                            "UNKNOWN",
                        },
                    }
                )
    return rows, sorted(gaps)


def _from_liquibase(body: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("contexts"), dict):
        raise ValueError("INVALID_MIGRATIONS_SHAPE")
    rows: list[dict[str, Any]] = []
    gaps: set[str] = set()
    for context_name in sorted(body["contexts"]):
        context = body["contexts"][context_name]
        if not isinstance(context, dict) or not isinstance(
            context.get("liquibaseBeans"), dict
        ):
            gaps.add("LIQUIBASE_BEANS_UNOBSERVED")
            continue
        parent = (
            context.get("parentId")
            if isinstance(context.get("parentId"), str)
            else None
        )
        for bean_name in sorted(context["liquibaseBeans"]):
            bean = context["liquibaseBeans"][bean_name]
            changes = bean.get("changeSets") if isinstance(bean, dict) else None
            if not isinstance(changes, list):
                gaps.add("LIQUIBASE_CHANGESETS_UNOBSERVED")
                continue
            for change in changes:
                if not isinstance(change, dict):
                    gaps.add("MIGRATION_RECORD_UNREADABLE")
                    continue
                raw = (
                    change.get("execType")
                    if isinstance(change.get("execType"), str)
                    else None
                )
                state, evidence = _state(raw, _LIQUIBASE)
                raw_id = change.get("id")
                change_id = raw_id if isinstance(raw_id, str) else "unidentified"
                checksum, checksum_type = _checksum(change.get("checksum"))
                algorithm = None
                if checksum and (matched := _ALGORITHM.match(checksum)):
                    algorithm = matched.group(1)
                rows.append(
                    {
                        "endpointId": "liquibase",
                        "contextId": context_name
                        if isinstance(context_name, str)
                        else None,
                        "parentContextId": parent,
                        "engine": "liquibase",
                        "migrationBean": str(bean_name)[:256],
                        "identity": f"{bean_name}:{change_id}"[:256],
                        "script": None,
                        "changeLog": change.get("changeLog")
                        if isinstance(change.get("changeLog"), str)
                        else None,
                        "changeSetId": change_id[:256],
                        "author": change.get("author")
                        if isinstance(change.get("author"), str)
                        else None,
                        "version": None,
                        "description": None,
                        "checksum": checksum,
                        "checksumType": checksum_type,
                        "checksumAlgorithm": algorithm,
                        "executionTimeMs": None,
                        "orderExecuted": change.get("orderExecuted")
                        if type(change.get("orderExecuted")) is int
                        and change["orderExecuted"] >= 0
                        else None,
                        "executedAt": change.get("dateExecuted")
                        if isinstance(change.get("dateExecuted"), str)
                        else None,
                        "rawState": raw[:64] if raw else None,
                        "state": state,
                        "stateEvidence": evidence,
                        "attentionRequired": state
                        in {
                            "FAILED",
                            "PENDING",
                            "OUT_OF_ORDER",
                            "MISSING_FAILED",
                            "UNKNOWN",
                        },
                    }
                )
    return rows, sorted(gaps)


def apply_expected(
    rows: list[dict[str, Any]], query: MigrationQuery
) -> tuple[list[dict[str, Any]], list[str], str]:
    if not query.expected:
        return rows, [], "unknown"
    observed = {
        (row["engine"], row["contextId"], row["migrationBean"], row["identity"]): row
        for row in rows
    }
    gaps: list[str] = []
    for expected in query.expected:
        key = (
            expected.engine,
            expected.context_id,
            expected.migration_bean,
            expected.identity,
        )
        current = observed.get(key)
        if current is None and query.expected_set_complete:
            rows.append(
                {
                    "endpointId": expected.engine,
                    "contextId": expected.context_id,
                    "parentContextId": None,
                    "engine": expected.engine,
                    "migrationBean": expected.migration_bean,
                    "identity": expected.identity,
                    "script": None,
                    "changeLog": None,
                    "changeSetId": None,
                    "author": None,
                    "version": expected.version,
                    "description": None,
                    "checksum": expected.checksum,
                    "checksumType": "string" if expected.checksum else "absent",
                    "checksumAlgorithm": None,
                    "executionTimeMs": None,
                    "orderExecuted": None,
                    "executedAt": None,
                    "rawState": None,
                    "state": "PENDING",
                    "stateEvidence": "baselineInferred",
                    "attentionRequired": True,
                }
            )
            continue
        if current and expected.checksum and current["checksum"] != expected.checksum:
            gaps.append("CHECKSUM_MISMATCH")
            current["attentionRequired"] = True
    return rows, gaps, "baselineInferred"


def normalize_migrations(
    flyway: Any | None, liquibase: Any | None, query: MigrationQuery
) -> tuple[list[dict[str, Any]], list[str], str]:
    rows: list[dict[str, Any]] = []
    gaps: list[str] = []
    if flyway is not None:
        parsed, parsed_gaps = _from_flyway(flyway)
        rows.extend(parsed)
        gaps.extend(parsed_gaps)
    if liquibase is not None:
        parsed, parsed_gaps = _from_liquibase(liquibase)
        rows.extend(parsed)
        gaps.extend(parsed_gaps)
    rows, expected_gaps, visibility = apply_expected(rows, query)
    gaps.extend(expected_gaps)
    kept = [row for row in rows if _keep(query, row)]
    items = []
    for index, row in enumerate(kept, start=1):
        row["id"] = f"migration_{index:06d}"
        items.append(row)
    return items, sorted(set(gaps)), visibility
