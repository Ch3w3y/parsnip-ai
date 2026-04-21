#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "psycopg[binary]>=3.1",
#   "pgvector>=0.3",
#   "python-dotenv>=1.0",
#   "httpx>=0.27",
# ]
# ///
"""
Generate a knowledge base intelligence report and publish it to Joplin.

Usage:
    uv run python scripts/kb_report.py
    uv run python scripts/kb_report.py --days 30 --notebook-id <joplin-folder-id>

The report is idempotent — re-running updates the same Joplin note.
Note ID is persisted in scripts/.kb_report_note_id.
"""

import argparse
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
JOPLIN_URL = os.environ.get("JOPLIN_SERVER_URL", "http://localhost:22300")
JOPLIN_EMAIL = os.environ.get("JOPLIN_ADMIN_EMAIL", "")
JOPLIN_PASSWORD = os.environ.get("JOPLIN_ADMIN_PASSWORD", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
STATE_FILE = Path(__file__).parent / ".kb_report_note_id"

REPORT_MODEL = "anthropic/claude-sonnet-4-6"

STOP_WORDS = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with","by",
    "from","as","is","was","are","were","be","been","being","have","has","had",
    "do","does","did","will","would","could","should","may","might","can","that",
    "this","these","those","it","its","he","she","they","we","you","i","me","him",
    "her","them","us","my","your","his","their","our","not","no","so","if","up",
    "out","all","about","after","before","between","into","through","during","than",
    "then","when","where","which","who","what","how","also","more","most","other",
    "some","such","new","over","under","while","said","one","two","three","first",
    "s","t","re","ve","d","ll","m","n","just","like","even","still","well",
    "since","both","each","few","many","much","very","now","here","there","say",
    "use","get","make","go","know","take","see","come","think","look","want",
    "give","find","tell","ask","seem","feel","try","leave","call","keep",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def db_connect():
    return psycopg.connect(DATABASE_URL)


def fetch_source_stats(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                source,
                COUNT(*) AS chunks,
                -- Wikipedia source_ids are "Title::chunk_N" — strip suffix for true article count
                COUNT(DISTINCT split_part(source_id, '::', 1)) AS articles,
                MIN(created_at) AS first_at,
                MAX(created_at) AS last_at
            FROM knowledge_chunks
            GROUP BY source
            ORDER BY chunks DESC
        """)
        rows = cur.fetchall()
    return [
        {"source": r[0], "chunks": r[1], "articles": r[2],
         "first_at": r[3], "last_at": r[4]}
        for r in rows
    ]


def fetch_daily_ingestion(conn, days: int = 30) -> list[dict]:
    """Chunks created per day over the last N days."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE(created_at) AS day, COUNT(*) AS chunks
            FROM knowledge_chunks
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY day
            ORDER BY day
        """, (days,))
        rows = cur.fetchall()
    return [{"day": r[0], "chunks": r[1]} for r in rows]


def fetch_latest_per_source(conn, n: int = 5) -> dict[str, list[dict]]:
    """Most recently inserted articles per source (de-duplicated by article title)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source,
                   split_part(source_id, '::', 1) AS article,
                   MAX(created_at) AS last_seen
            FROM knowledge_chunks
            GROUP BY source, article
            ORDER BY source, last_seen DESC
        """)
        rows = cur.fetchall()

    result: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for source, article, last_seen in rows:
        if source not in result:
            result[source] = []
            counts[source] = 0
        if counts[source] < n:
            result[source].append({"source_id": article, "last_seen": last_seen})
            counts[source] += 1
    return result


def fetch_news_words(conn, days: int = 30) -> Counter:
    """Word frequency from news content in the last N days."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT content FROM knowledge_chunks
            WHERE source = 'news'
              AND created_at >= NOW() - INTERVAL '%s days'
            LIMIT 500
        """, (days,))
        rows = cur.fetchall()

    counter: Counter = Counter()
    for (text,) in rows:
        words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
        counter.update(w for w in words if w not in STOP_WORDS)
    return counter


def fetch_ingestion_jobs(conn, n: int = 8) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source, status, processed, total, started_at, finished_at
            FROM ingestion_jobs
            ORDER BY id DESC LIMIT %s
        """, (n,))
        rows = cur.fetchall()
    return [
        {"source": r[0], "status": r[1], "processed": r[2], "total": r[3],
         "started_at": r[4], "finished_at": r[5]}
        for r in rows
    ]


def fetch_memory_stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE deleted_at IS NULL) AS active,
                   MAX(importance) AS max_importance,
                   COUNT(DISTINCT category) AS categories
            FROM agent_memories
        """)
        r = cur.fetchone()
    return {"total": r[0], "active": r[1], "max_importance": r[2], "categories": r[3]}


# ── LLM narrative ─────────────────────────────────────────────────────────────

def generate_narrative(stats_summary: str) -> str:
    """Call claude-sonnet-4-6 via OpenRouter to write the executive summary."""
    if not OPENROUTER_KEY:
        return "_[OpenRouter API key not set — narrative analysis unavailable]_"

    prompt = f"""You are an AI research assistant analysing the state of a personal knowledge base.
Given the following statistics, write a concise 3-5 paragraph executive summary that:
1. Summarises the current state of the knowledge base
2. Notes what's well-covered and what's missing
3. Identifies interesting patterns in the data
4. Suggests next ingestion priorities
5. Comments on the knowledge horizon balance (news vs research vs reference)

Be specific about numbers. Write in a direct, analytical tone. Use markdown formatting.

KNOWLEDGE BASE STATISTICS:
{stats_summary}"""

    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "HTTP-Referer": "https://github.com/pi-agent",
            },
            json={
                "model": REPORT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"_[Narrative generation failed: {e}]_"


# ── Markdown builders ─────────────────────────────────────────────────────────

def _bar(count: int, max_count: int, width: int = 24) -> str:
    if max_count == 0:
        return "░" * width
    filled = max(1, int(count / max_count * width))
    return "█" * filled + "░" * (width - filled)


def build_report(
    source_stats: list[dict],
    daily: list[dict],
    latest: dict[str, list[dict]],
    word_freq: Counter,
    jobs: list[dict],
    memory_stats: dict,
    narrative: str,
    days: int,
) -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    total_chunks = sum(s["chunks"] for s in source_stats)
    total_articles = sum(s["articles"] for s in source_stats)

    lines = [
        f"# Knowledge Base Intelligence Report",
        f"*Generated: {ts} using {REPORT_MODEL}*",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        narrative,
        "",
        "---",
        "",
        "## Knowledge Base Overview",
        "",
        f"**{total_chunks:,}** total chunks · **{total_articles:,}** unique articles across **{len(source_stats)}** sources",
        "",
        "| Source | Chunks | Articles | First Ingested | Last Updated |",
        "|--------|-------:|--------:|----------------|--------------|",
    ]

    for s in source_stats:
        first = s["first_at"].strftime("%Y-%m-%d") if s["first_at"] else "—"
        last = s["last_at"].strftime("%Y-%m-%d %H:%M") if s["last_at"] else "—"
        lines.append(
            f"| {s['source']} | {s['chunks']:,} | {s['articles']:,} | {first} | {last} |"
        )

    # Source distribution — pie when multiple sources, horizon bar when single source
    lines += ["", "## Source Distribution", ""]
    if len(source_stats) > 1:
        lines += [
            "```mermaid",
            'pie title "Chunks by Knowledge Source"',
        ]
        for s in source_stats:
            lines.append(f'    "{s["source"]}" : {s["chunks"]}')
        lines += ["```", ""]
    else:
        # Single source: show knowledge horizon balance (actual vs target)
        horizon_sources = {
            "wikipedia": ("Encyclopaedic", 0),
            "arxiv": ("Research", 0),
            "biorxiv": ("Research", 0),
            "news": ("News", 0),
            "joplin_notes": ("Personal Notes", 0),
        }
        actual = {s["source"]: s["chunks"] for s in source_stats}
        lines += [
            "```mermaid",
            "xychart-beta",
            '    title "Knowledge Horizon Balance (chunks)"',
            '    x-axis ["Encyclopaedic", "Research", "News", "Personal Notes"]',
            f'    y-axis "Chunks" 0 --> {max(total_chunks + 100, 1000)}',
            f'    bar [{actual.get("wikipedia", 0)}, '
            f'{actual.get("arxiv", 0) + actual.get("biorxiv", 0)}, '
            f'{actual.get("news", 0)}, '
            f'{actual.get("joplin_notes", 0)}]',
            "```",
            "",
            "> **Horizon balance:** Currently 100% encyclopaedic. "
            "Start the scheduler (`./pi-ctl.sh ingest start`) to populate "
            "Research and News layers.",
            "",
        ]

    # Mermaid system topology graph — use <br/> for line breaks in node labels
    lines += [
        "## System Topology",
        "",
        "```mermaid",
        "graph LR",
        '    User(["👤 User"]) --> OWU["OpenWebUI<br/>:3000"]',
        '    OWU --> PL["Pipelines<br/>:9099"]',
        '    PL --> AG["LangGraph Agent<br/>:8000"]',
        '    AG --> VDB[("PostgreSQL<br/>pgvector")]',
        '    AG --> JOP["Joplin Server<br/>:22300"]',
        '    SCHED["Scheduler"] --> VDB',
        '    WIKI["Wiki Ingest<br/>host process"] --> VDB',
        '    OLLAMA["Ollama<br/>mxbai-embed-large<br/>:11434"] --> AG',
        '    OLLAMA --> SCHED',
        '    OLLAMA --> WIKI',
    ]
    for s in source_stats:
        safe = s["source"].replace("_", "")
        lines.append(f'    VDB --> SRC_{safe}["{s["source"]}<br/>{s["chunks"]:,} chunks"]')
    lines += ["```", ""]

    # Daily ingestion xychart
    if daily:
        # Fill gaps — build a complete date series
        all_days = {}
        for d in daily:
            all_days[d["day"]] = d["chunks"]

        start = now.date() - timedelta(days=days - 1)
        labels, values = [], []
        for i in range(days):
            day = start + timedelta(days=i)
            labels.append(day.strftime("%-d %b"))
            values.append(all_days.get(day, 0))

        max_val = max(values) if values else 1
        lines += [
            "## Ingestion Timeline",
            "",
            f"*Chunks ingested per day — last {days} days*",
            "",
            "```mermaid",
            "xychart-beta",
            f'    title "Daily Chunks Ingested (last {days} days)"',
            '    x-axis [' + ", ".join(f'"{l}"' for l in labels) + ']',
            f'    y-axis "Chunks" 0 --> {max_val + 100}',
            f'    bar [{", ".join(str(v) for v in values)}]',
            "```",
            "",
        ]

    # Ingestion jobs
    if jobs:
        lines += [
            "## Ingestion Pipeline Status",
            "",
            "| Source | Status | Processed | Total | Started | Duration |",
            "|--------|--------|----------:|------:|---------|----------|",
        ]
        for j in jobs:
            started = j["started_at"].strftime("%Y-%m-%d %H:%M") if j["started_at"] else "—"
            if j["started_at"] and j["finished_at"]:
                dur = j["finished_at"] - j["started_at"]
                duration = f"{int(dur.total_seconds() // 60)}m"
            elif j["started_at"] and j["status"] == "running":
                dur = now - j["started_at"]
                duration = f"{int(dur.total_seconds() // 60)}m (running)"
            else:
                duration = "—"
            total_str = str(j["total"]) if j["total"] else "—"
            processed_str = f"{j['processed']:,}" if j["processed"] else "0"
            lines.append(
                f"| {j['source']} | {j['status']} | {processed_str} | {total_str} | {started} | {duration} |"
            )
        lines.append("")

    # Latest additions
    lines += ["## Latest Additions", ""]
    for source, items in latest.items():
        lines.append(f"### {source}")
        if not items:
            lines.append("*No data*")
        else:
            for item in items:
                ts_str = item["last_seen"].strftime("%Y-%m-%d %H:%M") if item["last_seen"] else ""
                lines.append(f"- `{item['source_id'][:80]}` — {ts_str}")
        lines.append("")

    # Agent memory
    if memory_stats["total"]:
        lines += [
            "## Agent Memory",
            "",
            f"| Total Memories | Active | Categories | Max Importance |",
            f"|---------------:|-------:|-----------:|---------------:|",
            f"| {memory_stats['total']} | {memory_stats['active']} | {memory_stats['categories']} | {memory_stats['max_importance'] or '—'} |",
            "",
        ]

    # Word cloud
    if word_freq:
        top = word_freq.most_common(40)
        max_count = top[0][1]

        lines += [
            f"## News Intelligence — Last {days} Days",
            "",
            "### Top Terms",
            "",
        ]

        # Visual word cloud using font-weight analogy: bold top 10, italic next 10, plain rest
        top10 = [f"**{w}**" for w, _ in top[:10]]
        mid10 = [f"*{w}*" for w, _ in top[10:20]]
        rest = [w for w, _ in top[20:]]
        lines.append(" · ".join(top10 + mid10 + rest))
        lines += [
            "",
            "### Term Frequency",
            "",
            "| Term | Count | Frequency |",
            "|------|------:|-----------|",
        ]
        for word, count in top[:30]:
            lines.append(f"| {word} | {count} | `{_bar(count, max_count, 20)}` |")
        lines.append("")
    else:
        lines += [
            f"## News Intelligence — Last {days} Days",
            "",
            "*No news content in the last 30 days. Start the scheduler to begin news ingestion.*",
            "",
        ]

    lines += [
        "---",
        f"*Report generated by `scripts/kb_report.py` · Model: `{REPORT_MODEL}` · {ts}*",
    ]

    return "\n".join(lines)


# ── Joplin helpers ────────────────────────────────────────────────────────────

def _iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _serialize_note(note_id: str, title: str, body: str, parent_id: str, now_ms: int) -> bytes:
    ts = _iso_ms(now_ms)
    meta = "\n".join([
        f"id: {note_id}", f"parent_id: {parent_id}", f"title: {title}",
        f"created_time: {ts}", f"updated_time: {ts}", "is_conflict: 0",
        "latitude: 0.00000000", "longitude: 0.00000000", "altitude: 0.0000",
        "author: ", "source_url: ", "is_todo: 0", "todo_due: 0",
        "todo_completed: 0", "source: joplinapp-server",
        "source_application: net.cozic.joplin-server", "application_data: ",
        "order: 0", f"user_created_time: {ts}", f"user_updated_time: {ts}",
        "encryption_cipher_text: ", "encryption_applied: 0",
        "markup_language: 1", "is_shared: 0", "share_id: ",
        "conflict_original_id: ", "master_key_id: ", "user_data: ",
        "deleted_time: 0", "type_: 1",
    ])
    return f"{body}\n\n{meta}".encode("utf-8")


def joplin_auth(client: httpx.Client) -> str:
    r = client.post(
        f"{JOPLIN_URL}/api/sessions",
        json={"email": JOPLIN_EMAIL, "password": JOPLIN_PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["id"]


def joplin_put_note(client: httpx.Client, token: str, name: str, content: bytes):
    r = client.put(
        f"{JOPLIN_URL}/api/items/{quote(name, safe='')}/content",
        headers={"X-API-AUTH": token, "Content-Type": "application/octet-stream"},
        content=content,
        timeout=30,
    )
    r.raise_for_status()


def load_note_id() -> str | None:
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip() or None
    return None


def save_note_id(note_id: str):
    STATE_FILE.write_text(note_id)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate KB report → Joplin note")
    parser.add_argument("--days", type=int, default=30, help="Days window for news + timeline")
    parser.add_argument("--notebook-id", default="", help="Joplin notebook (folder) UUID")
    args = parser.parse_args()

    print("Connecting to database...")
    conn = db_connect()

    print("Fetching stats...")
    source_stats = fetch_source_stats(conn)
    daily = fetch_daily_ingestion(conn, args.days)
    latest = fetch_latest_per_source(conn, n=5)
    word_freq = fetch_news_words(conn, args.days)
    jobs = fetch_ingestion_jobs(conn)
    memory_stats = fetch_memory_stats(conn)
    conn.close()

    # Build stats summary for LLM
    stats_lines = [f"Total chunks: {sum(s['chunks'] for s in source_stats):,}"]
    for s in source_stats:
        stats_lines.append(
            f"  {s['source']}: {s['chunks']:,} chunks, {s['articles']:,} articles, "
            f"last updated {s['last_at'].strftime('%Y-%m-%d') if s['last_at'] else 'never'}"
        )
    if word_freq:
        top_words = ", ".join(f"{w}({c})" for w, c in word_freq.most_common(15))
        stats_lines.append(f"Top news terms (last {args.days}d): {top_words}")
    stats_lines.append(f"Agent memories: {memory_stats['active']} active")
    running_jobs = [j for j in jobs if j["status"] == "running"]
    if running_jobs:
        stats_lines.append(f"Active ingestion jobs: {', '.join(j['source'] for j in running_jobs)}")

    print(f"Generating narrative via {REPORT_MODEL}...")
    narrative = generate_narrative("\n".join(stats_lines))

    print("Building report...")
    report_md = build_report(
        source_stats, daily, latest, word_freq, jobs, memory_stats, narrative, args.days
    )

    title = f"KB Intelligence Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    print("Publishing to Joplin...")
    if not JOPLIN_EMAIL or not JOPLIN_PASSWORD:
        print("Joplin not configured (JOPLIN_ADMIN_EMAIL / JOPLIN_ADMIN_PASSWORD not set)")
        print("\n--- REPORT PREVIEW ---\n")
        print(report_md[:2000])
        return

    note_id = load_note_id()
    if not note_id:
        note_id = uuid.uuid4().hex

    now_ms = int(time.time() * 1000)
    serialized = _serialize_note(note_id, title, report_md, args.notebook_id, now_ms)
    name = f"root:/{note_id}.md:"

    try:
        with httpx.Client() as client:
            token = joplin_auth(client)
            joplin_put_note(client, token, name, serialized)
        save_note_id(note_id)
        print(f"Report published!")
        print(f"Note ID: {note_id}")
        print(f"Open in Joplin: joplin://x-callback-url/openNote?id={note_id}")
    except Exception as e:
        print(f"Joplin error: {e}")
        print("\nReport markdown saved to /tmp/kb_report.md")
        Path("/tmp/kb_report.md").write_text(report_md)


if __name__ == "__main__":
    main()
