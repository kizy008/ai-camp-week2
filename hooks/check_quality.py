#!/usr/bin/env python3
"""5-dimension quality scoring for knowledge entry JSON files."""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple, TypeAlias


GRADE_A = 80
GRADE_B = 60

STOPWORDS_CN = frozenset({
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑",
    "颗粒度", "对齐", "拉通", "沉淀", "强大的", "革命性的",
})
STOPWORDS_EN = frozenset({
    "groundbreaking", "revolutionary", "game-changing", "cutting-edge",
    "state-of-the-art", "best-in-class", "next-generation", "paradigm-shift",
})

TECH_KEYWORDS = frozenset({
    "python", "javascript", "typescript", "go", "rust", "java", "c++", "c#",
    "api", "sdk", "json", "yaml", "docker", "kubernetes", "git", "linux",
    "http", "https", "tcp", "udp", "sql", "nosql", "redis", "postgresql",
    "mysql", "mongodb", "aws", "azure", "gcp", "ai", "ml", "llm", "gpt",
    "openai", "agent", "tool-use", "code-generation", "framework",
    "react", "vue", "angular", "node", "deno", "bun", "npm", "webpack",
    "vite", "esbuild", "terraform", "ansible", "prometheus", "grafana",
})

VALID_TAGS = frozenset({
    "agent", "api", "architecture", "automation", "backend", "best-practice",
    "cli", "cloud", "code-generation", "database", "debugging", "deploy",
    "design-pattern", "devops", "docker", "documentation", "efficiency",
    "environment", "error-handling", "es6", "express", "fastapi",
    "finops", "flask", "frontend", "fullstack", "git", "github-actions",
    "go", "gradle", "graphql", "java", "javascript", "json", "jwt",
    "kubernetes", "llm", "logging", "machine-learning", "makefile",
    "microservices", "middleware", "migration", "mobile", "monitoring",
    "nestjs", "nextjs", "node", "npm", "observability", "openapi",
    "opensource", "optimization", "orm", "performance", "pipe",
    "postgresql", "prometheus", "prototype", "python", "react",
    "react-native", "readme", "redis", "refactoring", "restful",
    "rust", "sass", "scalability", "security", "serverless", "spring",
    "sql", "sqlite", "ssh", "startup", "svelte", "swift", "swr",
    "tailwindcss", "tdd", "telemetry", "testing", "tool-use", "tracing",
    "tutorial", "typescript", "ubuntu", "unity", "validation", "vscode",
    "webassembly", "webpack", "websocket", "workflow",
})

VALID_STATUSES = frozenset({"draft", "review", "published", "archived"})

ScoreType: TypeAlias = float


@dataclass
class DimensionScore:
    name: str
    score: float
    max_score: float
    detail: str = ""

    @property
    def percentage(self) -> float:
        return (self.score / self.max_score * 100) if self.max_score > 0 else 0.0

    @property
    def grade(self) -> str:
        pct = self.percentage
        return "A" if pct >= 80 else "B" if pct >= 60 else "C"

    def bar(self, width: int = 30) -> str:
        filled = int(self.percentage / 100 * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"{self.name} |{bar}| {self.score:.0f}/{self.max_score} ({self.grade})"


@dataclass
class QualityReport:
    file_path: Path
    dimensions: List[DimensionScore] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return sum(d.score for d in self.dimensions)

    @property
    def max_total(self) -> float:
        return sum(d.max_score for d in self.dimensions)

    @property
    def overall_percentage(self) -> float:
        return (self.total_score / self.max_total * 100) if self.max_total > 0 else 0.0

    @property
    def overall_grade(self) -> str:
        pct = self.overall_percentage
        return "A" if pct >= GRADE_A else "B" if pct >= GRADE_B else "C"

    def print_report(self) -> None:
        print(f"\n📄 {self.file_path}")
        for d in self.dimensions:
            print(f"  {d.bar()}  {d.detail}")
        print(
            f"  总分: {self.total_score:.0f}/{self.max_total}  "
            f"({self.overall_percentage:.0f}%)  等级: {self.overall_grade}"
        )


def score_summary_quality(record: dict) -> DimensionScore:
    summary = record.get("summary", "")
    score = 0.0
    details: list[str] = []

    length = len(summary)
    if length >= 50:
        score = 25.0
        details.append(f"长度 {length} 字, 满分")
    elif length >= 20:
        score = 15.0
        details.append(f"长度 {length} 字, 基本分")
    else:
        score = max(0.0, length / 20 * 8)
        details.append(f"长度 {length} 字, 不足 20 字")

    kw_count = sum(1 for kw in TECH_KEYWORDS if kw.lower() in summary.lower())
    if kw_count >= 3:
        bonus = 5.0
        score = min(25.0, score + bonus)
        details.append(f"含 {kw_count} 个技术关键词 +{bonus:.0f} 分")
    elif kw_count >= 1:
        bonus = 2.0
        score = min(25.0, score + bonus)
        details.append(f"含 {kw_count} 个技术关键词 +{bonus:.0f} 分")

    detail = "; ".join(details)
    return DimensionScore(name="摘要质量", score=score, max_score=25.0, detail=detail)


def score_tech_depth(record: dict) -> DimensionScore:
    score = record.get("score")
    if score is None:
        return DimensionScore(name="技术深度", score=0.0, max_score=25.0, detail="无 score 字段")

    if isinstance(score, (int, float)) and 1 <= score <= 10:
        mapped = score / 10.0 * 25.0
        detail = f"score={score} 映射为 {mapped:.1f}/25"
        return DimensionScore(name="技术深度", score=mapped, max_score=25.0, detail=detail)

    return DimensionScore(name="技术深度", score=0.0, max_score=25.0, detail=f"score 值无效: {score}")


def score_format(record: dict) -> DimensionScore:
    score = 0.0
    details: list[str] = []

    # id format
    if re.match(r"^[a-z0-9_-]+-(\d{8})-(\d{3})$", record.get("id", ""), re.IGNORECASE):
        score += 4.0
        details.append("id 格式正确")
    else:
        details.append("id 格式有误")

    # title
    title = record.get("title", "")
    if isinstance(title, str) and len(title) >= 2:
        score += 4.0
        details.append("title 合法")
    else:
        details.append("title 缺失或过短")

    # source_url
    if re.match(r"^https?://", record.get("source_url", "")):
        score += 4.0
        details.append("source_url 合法")
    else:
        details.append("source_url 有误")

    # status
    status = record.get("status", "")
    if status in VALID_STATUSES:
        score += 4.0
        details.append(f"status={status}")
    else:
        details.append("status 无效")

    # timestamp in id
    id_val = record.get("id", "")
    m = re.match(r"^[a-z0-9_-]+-(\d{8})-(\d{3})$", id_val, re.IGNORECASE)
    if m:
        ts = m.group(1)
        year, month, day = int(ts[:4]), int(ts[4:6]), int(ts[6:8])
        if 2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
            score += 4.0
            details.append(f"时间戳 {ts} 合法")
        else:
            details.append(f"时间戳 {ts} 不合法")
    else:
        details.append("id 中无有效时间戳")

    detail = "; ".join(details)
    return DimensionScore(name="格式规范", score=score, max_score=20.0, detail=detail)


def score_tag_quality(record: dict) -> DimensionScore:
    tags = record.get("tags", [])
    if not isinstance(tags, list):
        return DimensionScore(name="标签精度", score=0.0, max_score=15.0, detail="tags 非列表")

    if len(tags) == 0:
        return DimensionScore(name="标签精度", score=0.0, max_score=15.0, detail="无标签")

    if len(tags) > 8:
        base_score = 3.0
        extra_detail = f"标签过多 ({len(tags)} 个), 最多 8 个"
    else:
        base_score = 8.0
        extra_detail = ""

    valid_count = sum(1 for t in tags if isinstance(t, str) and t in VALID_TAGS)
    bonus = min(valid_count * 2.0, 7.0)
    score = min(15.0, base_score + bonus)

    detail_parts: list[str] = []
    if extra_detail:
        detail_parts.append(extra_detail)
    detail_parts.append(f"标签 {len(tags)} 个, 标准标签 {valid_count}/{len(tags)}")
    if bonus > 0:
        detail_parts.append(f"标准标签奖励 +{bonus:.0f}")

    return DimensionScore(name="标签精度", score=score, max_score=15.0, detail="; ".join(detail_parts))


def score_stopword_detection(record: dict) -> DimensionScore:
    text_fields = [
        record.get("title", ""),
        record.get("summary", ""),
    ]
    combined = " ".join(str(v) for v in text_fields)

    cn_hits = [w for w in STOPWORDS_CN if w in combined]
    en_hits = [w for w in STOPWORDS_EN if w.lower() in combined.lower()]

    total_hits = len(cn_hits) + len(en_hits)
    if total_hits == 0:
        return DimensionScore(name="空洞词检测", score=15.0, max_score=15.0, detail="未发现空洞词 ✓")

    deduction = min(total_hits * 5.0, 15.0)
    score = 15.0 - deduction
    hit_list = cn_hits + en_hits

    return DimensionScore(
        name="空洞词检测",
        score=max(0.0, score),
        max_score=15.0,
        detail=f"发现 {total_hits} 个空洞词: {', '.join(hit_list)}",
    )


def expand_files(paths: List[str]) -> List[Path]:
    result: list[Path] = []
    for p in paths:
        path = Path(p)
        if "*" in path.name or "?" in path.name:
            matches = sorted(path.parent.glob(path.name))
            result.extend(matches)
        else:
            result.append(path)
    return sorted(set(result))


def validate_and_score(record: Any, file_path: Path) -> Optional[QualityReport]:
    if not isinstance(record, dict):
        return None

    dims = [
        score_summary_quality(record),
        score_tech_depth(record),
        score_format(record),
        score_tag_quality(record),
        score_stopword_detection(record),
    ]
    return QualityReport(file_path=file_path, dimensions=dims)


def load_records(file_path: Path) -> Tuple[Optional[List[Any]], Optional[str]]:
    try:
        with file_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return None, str(exc)

    if isinstance(data, dict):
        return [data], None
    if isinstance(data, list):
        return data, None
    return [data], None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="知识条目 5 维度质量评分",
        usage="python hooks/check_quality.py <json_file> [json_file2 ...]",
    )
    parser.add_argument("files", nargs="+", help="一个或多个 JSON 文件（支持通配符 *.json）")
    args = parser.parse_args()

    file_paths = expand_files(args.files)
    if not file_paths:
        print("错误: 未找到匹配的 JSON 文件", file=sys.stderr)
        sys.exit(1)

    has_c = False
    for fp in file_paths:
        records, err = load_records(fp)
        if records is None:
            print(f"\n⚠️  {fp}: 无法读取 ({err})", file=sys.stderr)
            has_c = True
            continue

        for record in records:
            report = validate_and_score(record, fp)
            if report is None:
                print(f"\n⚠️  {fp}: 顶层不是 dict, 跳过评分", file=sys.stderr)
                has_c = True
                continue

            report.print_report()
            if report.overall_grade == "C":
                has_c = True

    if has_c:
        sys.exit(1)


if __name__ == "__main__":
    main()
