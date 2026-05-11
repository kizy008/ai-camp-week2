#!/usr/bin/env python3
"""Validate knowledge entry JSON files."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple


REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = frozenset({"draft", "review", "published", "archived"})
VALID_AUDIENCES = frozenset({"beginner", "intermediate", "advanced"})

ID_PATTERN = re.compile(r"^[a-z0-9_-]+-(\d{8})-(\d{3})$", re.IGNORECASE)
URL_PATTERN = re.compile(r"^https?://")


def expand_files(paths: List[str]) -> List[Path]:
    result: list[Path] = []
    for p in paths:
        path = Path(p)
        matches = sorted(path.parent.glob(path.name)) if "*" in path.name or "?" in path.name else [path]
        result.extend(matches)
    return sorted(set(result)) or [Path(p) for p in paths if not any(c in p for c in "*?")]


def validate_record(record: Any, file_path: Path) -> List[str]:
    errors: list[str] = []

    if not isinstance(record, dict):
        errors.append(f"{file_path}: 顶层元素必须是 dict 类型")
        return errors

    for field_name, field_type in REQUIRED_FIELDS.items():
        if field_name not in record:
            errors.append(f"{file_path}: 缺少必填字段 '{field_name}'")
        elif not isinstance(record[field_name], field_type):
            errors.append(
                f"{file_path}: 字段 '{field_name}' 类型错误，期望 {field_type.__name__}，实际 {type(record[field_name]).__name__}"
            )

    if errors:
        return errors

    if not ID_PATTERN.match(record["id"]):
        errors.append(
            f"{file_path}: ID '{record['id']}' 格式无效，期望格式 {{source}}-YYYYMMDD-NNN"
        )

    if record["status"] not in VALID_STATUSES:
        errors.append(
            f"{file_path}: status '{record['status']}' 无效，可选值: {', '.join(sorted(VALID_STATUSES))}"
        )

    if not URL_PATTERN.match(record["source_url"]):
        errors.append(f"{file_path}: source_url '{record['source_url']}' 不是合法的 URL")

    if len(record["summary"]) < 20:
        errors.append(
            f"{file_path}: summary 至少需要 20 个字符，当前 {len(record['summary'])} 个字符"
        )

    if len(record["tags"]) < 1:
        errors.append(f"{file_path}: tags 至少需要一个标签")

    for idx, tag in enumerate(record["tags"]):
        if not isinstance(tag, str):
            errors.append(f"{file_path}: tags[{idx}] 必须是 str 类型")
            break

    if "score" in record:
        score = record["score"]
        if not isinstance(score, (int, float)) or not (1 <= score <= 10):
            errors.append(
                f"{file_path}: score '{score}' 无效，必须在 1-10 范围内"
            )

    if "audience" in record:
        audience = record["audience"]
        if audience not in VALID_AUDIENCES:
            errors.append(
                f"{file_path}: audience '{audience}' 无效，可选值: {', '.join(sorted(VALID_AUDIENCES))}"
            )

    return errors


def validate_file(file_path: Path) -> Tuple[bool, List[str]]:
    errors: list[str] = []

    try:
        with file_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return False, [f"{file_path}: JSON 解析失败: {exc}"]
    except OSError as exc:
        return False, [f"{file_path}: 文件读取失败: {exc}"]

    records = [data] if isinstance(data, dict) else data if isinstance(data, list) else [data]

    for record in records:
        errors.extend(validate_record(record, file_path))

    return len(errors) == 0, errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="校验知识条目 JSON 文件",
        usage="python hooks/validate_json.py <json_file> [json_file2 ...]",
    )
    parser.add_argument(
        "files", nargs="+", help="一个或多个 JSON 文件（支持通配符 *.json）"
    )
    args = parser.parse_args()

    file_paths = expand_files(args.files)

    if not file_paths:
        print("错误: 未找到匹配的 JSON 文件", file=sys.stderr)
        sys.exit(1)

    all_errors: list[str] = []
    passes = 0
    failures = 0

    for fp in file_paths:
        ok, errors = validate_file(fp)
        if ok:
            passes += 1
        else:
            failures += 1
            all_errors.extend(errors)

    total = passes + failures

    if all_errors:
        for err in all_errors:
            print(err, file=sys.stderr)

    print(f"\n汇总: 共 {total} 个文件，通过 {passes} 个，失败 {failures} 个")

    if failures > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
