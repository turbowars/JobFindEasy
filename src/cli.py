"""CLI for the Job Intelligence Agent.

Usage:
  python -m src.cli init
  python -m src.cli scrape          # run all configured scrapers
  python -m src.cli prefilter       # run rule-based filter on new jobs
  python -m src.cli score           # LLM score the prefilter survivors
  python -m src.cli run             # full pipeline: scrape -> prefilter -> score -> notify
  python -m src.cli notify          # send daily strong-fit notification
  python -m src.cli stats           # quick summary of DB
"""
from __future__ import annotations

import json
import logging
import os

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from . import db
from .enrichment.prefilter import prefilter as run_prefilter
from .enrichment.llm_scorer import score_job, make_client, compute_tier
from .scrapers.runner import run_all_sync
from .notify import notify_strong_fits as do_notify
from .resume import autogen_resume_if_missing

console = Console()


@click.group()
def cli():
    """Job Intelligence Agent CLI."""
    pass


@cli.command()
def init():
    """Create the SQLite database and indexes."""
    db.init_db()
    console.print("[green]initialized[/] data/jobs.db")


@cli.command()
def scrape():
    """Run all configured scrapers and upsert results."""
    console.print("[cyan]scraping...[/]")
    jobs = run_all_sync()
    if not jobs:
        console.print("[yellow]no jobs returned[/]")
        return
    new, updated = db.upsert_many(jobs)
    console.print(f"[green]scraped {len(jobs)} jobs[/]: [bold]{new} new[/], {updated} updated (existing)")


@cli.command(name="prefilter")
def prefilter_cmd():
    """Run rule-based prefilter on jobs that haven't been filtered yet."""
    pending = db.get_unfiltered()
    console.print(f"[cyan]prefiltering {len(pending)} jobs...[/]")
    passed = 0
    for j in pending:
        ok, reason, sponsorship = run_prefilter(j["title"], j["description"] or "")
        db.update_prefilter(j["hash"], ok, reason, sponsorship)
        if ok:
            passed += 1
    console.print(f"[green]prefilter done[/]: {passed}/{len(pending)} passed")


@cli.command()
@click.option("--limit", default=200, help="Max jobs to LLM-score this run")
def score(limit: int):
    """LLM-score jobs that passed prefilter."""
    pending = db.get_unscored_passed()[:limit]
    if not pending:
        console.print("[yellow]nothing to score[/]")
        return

    client = make_client()
    model = os.environ.get("SCORING_MODEL", "anthropic/claude-haiku-4.5")
    console.print(f"[cyan]scoring {len(pending)} jobs with {model}...[/]")

    auto_resume_cap = int(os.environ.get("AUTO_RESUME_CAP_PER_CYCLE", "5"))
    scored = 0
    auto_resumes = 0
    for j in pending:
        result = score_job(
            client, model,
            title=j["title"], company=j["company"], location=j["location"],
            description=j["description"] or "", sponsorship=j["sponsorship_status"],
        )
        if not result:
            n = db.record_score_failure(j["hash"])
            if n >= 3:
                console.print(f"[yellow]dead-letter[/]: {j['title'][:40]} @ {j['company']} (failed {n}x)")
            continue
        total = int(result.get("total", 0))
        tier = result.get("tier") or compute_tier(total)
        breakdown = json.dumps({
            k: result.get(k) for k in
            ["title_match", "skills_match", "leadership_scope", "domain_alignment", "location_fit", "comp_confidence"]
        })
        rationale = result.get("rationale", "")
        db.update_score(j["hash"], total, breakdown, rationale, tier)
        scored += 1
        if tier == "strong" and total >= 80 and auto_resumes < auto_resume_cap:
            path = autogen_resume_if_missing(
                j["title"], j["company"], j["description"] or "",
                location=j.get("location") or "",
            )
            if path:
                auto_resumes += 1

    console.print(f"[green]scored {scored} jobs[/]")
    if auto_resumes:
        console.print(f"[green]auto-generated {auto_resumes} resume(s) for strong fits[/]")


@cli.command()
def notify():
    """Send notification of today's strong fits."""
    fits = db.get_strong_fits_today(min_score=80)
    do_notify(fits)


@cli.command()
@click.option("--score-limit", default=200, help="Max jobs to LLM-score this run")
@click.pass_context
def run(ctx, score_limit: int):
    """Full pipeline: init -> scrape -> prefilter -> score -> notify."""
    db.init_db()
    ctx.invoke(scrape)
    ctx.invoke(prefilter_cmd)
    ctx.invoke(score, limit=score_limit)
    ctx.invoke(notify)


@cli.command()
def stats():
    """Show a summary of the database."""
    df = db.to_dataframe()
    if df.empty:
        console.print("[yellow]database is empty[/]")
        return

    table = Table(title="Job Intelligence Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total jobs", str(len(df)))
    table.add_row("Sources", str(df["source"].nunique()))
    table.add_row("Companies", str(df["company"].nunique()))
    table.add_row("Prefilter passed", str(int(df["prefilter_passed"].sum())))
    table.add_row("Scored", str(int(df["score_total"].notna().sum())))
    table.add_row("Strong fits (80+)", str(int((df["score_total"] >= 80).sum())))
    table.add_row("Possible (60-79)", str(int(((df["score_total"] >= 60) & (df["score_total"] < 80)).sum())))
    table.add_row("Sponsorship denied", str(int((df["sponsorship_status"] == "denied").sum())))
    for s in ("shortlisted", "applying", "applied", "interviewing", "offer", "closed"):
        n = int((df["status"] == s).sum())
        if n:
            table.add_row(s.capitalize(), str(n))
    console.print(table)

    top = df[df["score_total"].notna()].nlargest(10, "score_total")[
        ["score_total", "tier", "title", "company", "source"]
    ]
    if not top.empty:
        t2 = Table(title="Top 10 fits")
        t2.add_column("Score", justify="right")
        t2.add_column("Tier")
        t2.add_column("Title")
        t2.add_column("Company")
        t2.add_column("Source")
        for _, row in top.iterrows():
            t2.add_row(
                str(int(row["score_total"])), str(row["tier"]),
                str(row["title"])[:50], str(row["company"])[:30], str(row["source"]),
            )
        console.print(t2)


if __name__ == "__main__":
    cli()
