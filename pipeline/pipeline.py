"""
AI 知识库四步流水线：采集 → 分析 → 整理 → 保存

运行方式：
    python pipeline/pipeline.py --sources github,rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --sources github --step 1 --step 2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv

# 添加项目根目录到 path，以便导入 model_client
sys.path.insert(0, str(Path(__file__).parent))
from model_client import (
    create_provider,
    chat_with_retry,
    estimate_cost,
    get_tracker,
    LLMResponse,
)


load_dotenv()
logger = logging.getLogger(__name__)

# ── 项目路径 ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"
RSS_CONFIG = Path(__file__).parent / "rss_sources.yaml"

# ── Step 1: 采集 ─────────────────────────────────────────────────────────


def collect_github(limit: int = 10) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关热门仓库。"""
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    query = "topic:ai OR topic:llm OR topic:machine-learning OR topic:deep-learning OR topic:artificial-intelligence OR topic:nlp OR topic:computer-vision OR topic:generative-ai OR topic:large-language-model stars:>50"
    url = "https://api.github.com/search/repositories"
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": min(limit, 30),
    }

    results: list[dict[str, Any]] = []
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        date_str = datetime.now().strftime("%Y%m%d")

        for i, repo in enumerate(data.get("items", [])[:limit]):
            now = datetime.now(timezone.utc).isoformat()
            results.append({
                "id": f"github-{date_str}-{i+1:03d}",
                "title": repo["full_name"],
                "source": "github",
                "source_url": repo["html_url"],
                "author": repo["owner"]["login"],
                "published_at": repo.get("pushed_at", ""),
                "raw_description": repo.get("description", "") or "",
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language", ""),
                "topics": repo.get("topics", []),
                "collected_at": now,
            })

        print(f"  GitHub 采集完成: {len(results)} 条")
    except requests.RequestException as e:
        logger.error("GitHub API 调用失败: %s", e)

    return results


def collect_rss(limit: int = 10) -> list[dict[str, Any]]:
    """从配置的 RSS 源采集内容。"""
    if not RSS_CONFIG.exists():
        logger.warning("RSS 配置文件不存在: %s", RSS_CONFIG)
        return []

    with open(RSS_CONFIG, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    results: list[dict[str, Any]] = []
    count = 0
    date_str = datetime.now().strftime("%Y%m%d")

    for source in sources:
        if count >= limit:
            break
        try:
            resp = requests.get(
                source["url"],
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30.0,
            )
            resp.raise_for_status()
            feed_text = resp.text

            # 简易 RSS 解析：提取 <item> 中的 <title>, <link>, <description>
            items = re.findall(
                r"<item[^>]*>.*?"
                r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>.*?"
                r"<link[^>]*>(.*?)</link>.*?"
                r"<description[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>.*?"
                r"</item>",
                feed_text,
                re.DOTALL,
            )

            for title, link, description in items:
                if count >= limit:
                    break
                title = title.strip()
                link = link.strip()
                if not title or not link:
                    continue

                now = datetime.now(timezone.utc).isoformat()
                count += 1
                results.append({
                    "id": f"rss-{date_str}-{count:03d}",
                    "title": title,
                    "source": f"rss:{source['name']}",
                    "source_url": link,
                    "author": source.get("name", "unknown"),
                    "published_at": now,
                    "raw_description": description.strip() if description else "",
                    "category": source.get("category", "general"),
                    "collected_at": now,
                })

            print(f"  RSS [{source['name']}] 解析到 {len(items)} 条")

        except requests.RequestException as e:
            logger.warning("RSS 源 [%s] 获取失败: %s", source["name"], e)

    return results


# ── Step 1 统一入口 ──────────────────────────────────────────────────────


def step_collect(sources: list[str], limit: int) -> list[dict[str, Any]]:
    """Step 1: 按数据源采集原始数据。"""
    print(f"\n{'='*60}")
    print(f"Step 1: 采集（sources={sources}, limit={limit}）")
    print(f"{'='*60}")

    all_items: list[dict[str, Any]] = []

    if "github" in sources:
        all_items.extend(collect_github(limit))
    if "rss" in sources:
        all_items.extend(collect_rss(limit))

    # 保存原始数据
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_file = RAW_DIR / f"raw_{timestamp}.json"
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"  采集到 {len(all_items)} 条原始数据")
    print(f"  保存到 {raw_file}")

    return all_items


# ── Step 2: 分析 ─────────────────────────────────────────────────────────

ANALYZE_PROMPT_TEMPLATE = """请分析以下 AI 技术内容，返回 JSON 格式的分析结果。

内容信息：
- 标题：{title}
- 来源：{source}
- 描述：{description}

字段约束：
- "audience" 必须从以下值中选择其一：beginner, intermediate, advanced, general
- "score" 必须是 1-10 之间的整数
- "tags" 至少包含 1 个标签
- "summary" 至少 20 个字符

请返回以下格式的 JSON（不要包含 markdown 代码块标记）：
{{
  "summary": "2-3 句话的技术摘要，说明核心内容和价值",
  "score": 7,
  "tags": ["tag1", "tag2"],
  "audience": "intermediate"
}}
"""


def _sanitize_analysis(
    analysis: dict[str, Any],
    item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """校验并修正 LLM 分析结果中各字段的合法性。

    Args:
        analysis: LLM 返回的分析结果 dict。
        item: 原始采集数据，用于 summary 回退。

    Returns:
        校验修正后的安全 dict。
    """
    safe = dict(analysis)

    # audience
    valid_audiences = {"beginner", "intermediate", "advanced", "general"}
    audience = safe.get("audience", "intermediate")
    if audience not in valid_audiences:
        logger.warning("audience '%s' 无效，回退为 'intermediate'", audience)
        safe["audience"] = "intermediate"

    # score
    score = safe.get("score", 5)
    if not isinstance(score, (int, float)) or not (1 <= score <= 10):
        logger.warning("score '%s' 无效，回退为 5", score)
        safe["score"] = 5

    # summary
    summary = safe.get("summary", "")
    if not isinstance(summary, str) or len(summary) < 20:
        fallback = (item or {}).get("raw_description", "")
        logger.warning("summary 过短（%d 字符），使用 raw_description 回退", len(summary))
        new_summary = (fallback or "暂无摘要")[:200]
        # 如果回退后仍然不够长，补到 20 字符
        if len(new_summary) < 20:
            new_summary = new_summary.ljust(20, "。")
        safe["summary"] = new_summary

    # tags
    tags = safe.get("tags", [])
    if not isinstance(tags, list) or len(tags) < 1:
        logger.warning("tags 无效，回退为 ['llm']")
        safe["tags"] = ["llm"]
    else:
        safe["tags"] = [t for t in tags if isinstance(t, str)][:5]

    # status
    valid_statuses = {"draft", "review", "published", "archived"}
    status = safe.get("status", "review")
    if status not in valid_statuses:
        logger.warning("status '%s' 无效，回退为 'draft'", status)
        safe["status"] = "draft"

    return safe


def step_analyze(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 2: 调用 LLM 对每条内容进行分析。"""
    print(f"\n{'='*60}")
    print(f"Step 2: 分析（{len(items)} 条内容）")
    print(f"{'='*60}")

    provider = create_provider()
    analyzed: list[dict[str, Any]] = []
    total_cost = 0.0

    for i, item in enumerate(items):
        print(f"  [{i+1}/{len(items)}] 分析: {item['title'][:50]}...")

        prompt = ANALYZE_PROMPT_TEMPLATE.format(
            title=item["title"],
            source=item["source"],
            description=item.get("raw_description", "无描述"),
        )

        try:
            response = chat_with_retry(
                provider,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个 AI 技术分析专家。"
                            "请严格按要求返回 JSON。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=500,
            )

            cost = estimate_cost(response.model, response.usage.prompt_tokens,
                                 response.usage.completion_tokens)
            total_cost += cost

            # 解析 LLM 返回的 JSON
            content = response.content.strip()
            content = re.sub(r"^```json\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            analysis = json.loads(content)

            # 校验 LLM 返回的字段合法性
            analysis = _sanitize_analysis(analysis, item)

            # 合并原始数据和分析结果
            enriched: dict[str, Any] = {**item, **analysis}
            enriched["status"] = "review"
            enriched["analyzed_at"] = datetime.now(timezone.utc).isoformat()
            analyzed.append(enriched)

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("分析结果解析失败: %s — %s", item["title"], e)
            enriched = {
                **item,
                "summary": item.get("raw_description", "")[:200],
                "score": 5,
                "tags": ["llm"],
                "audience": "intermediate",
                "status": "draft",
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            }
            analyzed.append(enriched)
    print(f"  分析完成: {len(analyzed)} 条")
    print(f"  估算总成本: ${total_cost:.6f}")
    return analyzed


# ── Step 3: 整理 ─────────────────────────────────────────────────────────


def step_organize(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 3: 去重、格式化、校验。"""
    print(f"\n{'='*60}")
    print(f"Step 3: 整理（{len(items)} 条内容）")
    print(f"{'='*60}")

    # 去重：按 source_url 去重
    seen_urls: set[str] = set()
    unique: list[dict[str, Any]] = []

    # 先读取已有文章的 URL
    if ARTICLES_DIR.exists():
        for f in ARTICLES_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
                    if "source_url" in existing:
                        seen_urls.add(existing["source_url"])
            except (json.JSONDecodeError, IOError):
                pass

    dedup_count = 0
    for item in items:
        url = item.get("source_url", "")
        if url in seen_urls:
            dedup_count += 1
            continue
        seen_urls.add(url)
        unique.append(item)

    # 格式标准化
    organized: list[dict[str, Any]] = []
    for item in unique:
        article = {
            "id": item.get("id", "unknown-000"),
            "title": item.get("title", ""),
            "source": item.get("source", "unknown"),
            "source_url": item.get("source_url", ""),
            "author": item.get("author", "unknown"),
            "published_at": item.get("published_at", ""),
            "collected_at": item.get("collected_at", ""),
            "summary": item.get("summary", ""),
            "score": max(1, min(10, item.get("score", 5))),
            "tags": item.get("tags", []),
            "audience": item.get("audience", "intermediate"),
            "status": item.get("status", "draft"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        organized.append(article)

    print(f"  去重: 移除 {dedup_count} 条重复")
    print(f"  整理后: {len(organized)} 条")
    return organized


# ── Step 4: 保存 ─────────────────────────────────────────────────────────


def step_save(items: list[dict[str, Any]], dry_run: bool = False) -> list[Path]:
    """Step 4: 将文章保存为独立 JSON 文件。"""
    print(f"\n{'='*60}")
    print(f"Step 4: 保存（{len(items)} 条内容，dry_run={dry_run}）")
    print(f"{'='*60}")

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    saved_files: list[Path] = []

    for item in items:
        filename = f"{item['id']}.json"
        filepath = ARTICLES_DIR / filename

        if dry_run:
            print(f"  [DRY RUN] 将保存: {filepath}")
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(item, f, ensure_ascii=False, indent=2)
            print(f"  已保存: {filepath}")

        saved_files.append(filepath)

    print(f"\n  共 {'模拟' if dry_run else ''}保存 {len(saved_files)} 个文件")
    return saved_files


# ── 流水线主流程 ─────────────────────────────────────────────────────────


def run_pipeline(
    sources: list[str],
    limit: int = 10,
    dry_run: bool = False,
    steps: list[int] | None = None,
) -> dict[str, Any]:
    """运行完整的四步流水线。

    Args:
        sources: 数据源列表
        limit: 每个源的最大采集数
        dry_run: 仅模拟运行
        steps: 要执行的步骤列表（1-4），默认全部执行
    """
    run_steps = set(steps) if steps else {1, 2, 3, 4}

    start_time = datetime.now()
    print(f"\n{'#'*60}")
    print(f"# AI 知识库流水线 — {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"# 数据源: {', '.join(sources)} | 限制: {limit} | DryRun: {dry_run}")
    print(f"# 执行步骤: {sorted(run_steps)}")
    print(f"{'#'*60}")

    raw_items: list[dict] = []
    analyzed_items: list[dict] = []
    organized_items: list[dict] = []
    saved_files: list[str] = []

    # Step 1: 采集
    if 1 in run_steps:
        raw_items = step_collect(sources, limit)
        if not raw_items:
            print("\n  没有采集到任何数据，流水线结束。")
            return {"collected": 0, "analyzed": 0, "saved": 0}

    # Step 2: 分析
    if 2 in run_steps and raw_items:
        analyzed_items = step_analyze(raw_items)

    # Step 3: 整理
    if 3 in run_steps and analyzed_items:
        organized_items = step_organize(analyzed_items)

    # Step 4: 保存
    if 4 in run_steps and organized_items:
        saved_files = step_save(organized_items, dry_run=dry_run)

    elapsed = (datetime.now() - start_time).total_seconds()
    stats = {
        "collected": len(raw_items),
        "analyzed": len(analyzed_items),
        "organized": len(organized_items),
        "saved": len(saved_files),
        "elapsed_seconds": round(elapsed, 1),
    }

    print(f"\n{'#'*60}")
    print(f"# 流水线完成！耗时 {elapsed:.1f} 秒")
    print(f"# 采集: {stats['collected']} → 分析: {stats['analyzed']} "
          f"→ 整理: {stats['organized']} → 保存: {stats['saved']}")
    print(f"{'#'*60}\n")

    get_tracker().report()
    return stats


# ── CLI 入口 ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI 知识库采集流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python pipeline/pipeline.py --sources github,rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --sources rss --limit 10
        """,
    )
    parser.add_argument(
        "--sources", type=str, default="github,rss",
        help="数据源，逗号分隔（默认: github,rss）",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="每个源的最大采集数量（默认: 10）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅模拟运行，不实际保存文件",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="显示详细日志",
    )
    parser.add_argument(
        "--step", type=int, action="append",
        help="指定执行的步骤（1-4），可多次使用，如 --step 1 --step 2",
    )
    parser.add_argument(
        "--provider", type=str, default=None,
        help="LLM 提供商（deepseek/qwen/openai），覆盖环境变量 LLM_PROVIDER",
    )
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    sources = [s.strip() for s in args.sources.split(",")]
    run_pipeline(
        sources=sources,
        limit=args.limit,
        dry_run=args.dry_run,
        steps=args.step,
    )


if __name__ == "__main__":
    main()
