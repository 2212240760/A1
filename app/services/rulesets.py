import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RuleSet


def normalize_and_validate_definition_json(definition_json: str) -> str:
    try:
        payload = json.loads(definition_json)
    except json.JSONDecodeError as e:
        raise ValueError("definition_json 不是合法 JSON") from e

    if not isinstance(payload, dict):
        raise ValueError("definition_json 顶层必须是对象")

    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise ValueError("definition_json.rules 必须是数组")

    allowed_severities = {"info", "warn", "error"}
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"rules[{idx}] 必须是对象")

        for key in ("name", "severity", "expr", "message_template"):
            val = rule.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"rules[{idx}].{key} 必须是非空字符串")

        if rule["severity"] not in allowed_severities:
            raise ValueError(
                f"rules[{idx}].severity 必须是 info/warn/error 之一"
            )

    return json.dumps(payload, ensure_ascii=False)


def list_rulesets(db: Session) -> list[RuleSet]:
    return db.execute(select(RuleSet).order_by(RuleSet.id.desc())).scalars().all()


def create_ruleset(
    db: Session,
    *,
    name: str,
    version: str,
    definition_json: str,
    created_by: int,
) -> RuleSet:
    normalized = normalize_and_validate_definition_json(definition_json)
    ruleset = RuleSet(
        name=name,
        version=version,
        definition_json=normalized,
        created_by=created_by,
        is_active=False,
    )
    db.add(ruleset)
    db.flush()
    return ruleset


def activate_ruleset(db: Session, *, ruleset_id: int) -> RuleSet:
    ruleset = db.get(RuleSet, ruleset_id)
    if not ruleset:
        raise ValueError("未找到 RuleSet")

    active = (
        db.execute(select(RuleSet).where(RuleSet.is_active.is_(True)))
        .scalars()
        .all()
    )
    for rs in active:
        rs.is_active = False

    ruleset.is_active = True
    db.flush()
    return ruleset
