"""CLI for the Job Intelligence Agent.

Usage:
  python -m src.cli init
  python -m src.cli scrape          # run all configured scrapers
  python -m src.cli prefilter       # run rule-based filter on new jobs
  python -m src.cli score           # LLM score the prefilter survivors
  python -m src.cli inject-csv FILE # bulk-inject job URLs from a CSV
  python -m src.cli run             # full pipeline: scrape -> prefilter -> score -> notify
  python -m src.cli notify          # send daily strong-fit notification
  python -m src.cli stats           # quick summary of DB
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

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
from .cover_letter import autogen_cover_letter_if_missing
from .enrichment.pipeline import enrich_scored
from .enrichment.prefilter import prefilter as run_prefilter
from .notify import notify_strong_fits as do_notify
from .resume import autogen_resume_if_missing
from .scrapers.runner import run_all_sync
from .scrapers.url_inject import inject_from_url

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
    console.print(
        f"[green]scraped {len(jobs)} jobs[/]: [bold]{new} new[/], {updated} updated (existing)"
    )


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

    console.print(f"[cyan]scoring {len(pending)} jobs...[/]")
    auto_resume_cap = int(os.environ.get("AUTO_RESUME_CAP_PER_CYCLE", "5"))
    scored = 0
    auto_resumes = 0
    for j in pending:
        r = enrich_scored(
            job_hash=j["hash"],
            title=j["title"],
            company=j["company"],
            location=j["location"],
            description=j["description"] or "",
        )
        if not r["scored"]:
            if r["score_fail_count"] >= 3:
                console.print(
                    f"[yellow]dead-letter[/]: {j['title'][:40]} @ {j['company']} "
                    f"(failed {r['score_fail_count']}x)"
                )
            continue
        scored += 1
        if r["tier"] == "strong" and (r["total"] or 0) >= 80 and auto_resumes < auto_resume_cap:
            path = autogen_resume_if_missing(
                j["title"],
                j["company"],
                j["description"] or "",
                location=j.get("location") or "",
            )
            if path:
                auto_resumes += 1
                # Pair the resume with a cover letter for top fits. Coupled
                # to the resume autogen so the daily cap stays unified;
                # IC-titled jobs are skipped silently inside the helper.
                autogen_cover_letter_if_missing(
                    j["title"],
                    j["company"],
                    j["description"] or "",
                    location=j.get("location") or "",
                )

    console.print(f"[green]scored {scored} jobs[/]")
    if auto_resumes:
        console.print(f"[green]auto-generated {auto_resumes} resume(s) for strong fits[/]")


# Positional layout of the recruiter-email export. The header has a duplicate
# "Title" column (display title at 2, title sub-score at 8), so the file is
# parsed positionally rather than with DictReader.
_CSV_HEADER = [
    "#",
    "Tier",
    "Title",
    "Company",
    "Location",
    "Work Mode",
    "Salary",
    "Score",
    "Title",
    "Skills",
    "Scope",
    "Domain",
    "Loc",
    "Comp",
    "Apply URL",
]
_COL_NUM, _COL_TITLE, _COL_COMPANY, _COL_URL = 0, 2, 3, 14


@cli.command(name="inject-csv")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--limit",
    default=0,
    help="Process only the first N rows that have a URL (0 = all).",
)
def inject_csv(csv_path: Path, limit: int):
    """Bulk-inject job URLs from a CSV.

    Reads only the `Apply URL` column (the CSV's score columns are ignored —
    every URL is fetched and scored fresh by the app's own rubric). Each URL
    runs through the normal pipeline: fetch + extract -> prefilter -> score,
    then a tailored resume + cover letter for strong fits. Rows with no URL or
    that fail to fetch are skipped and written to a sidecar
    `<name>_skipped.csv` so they can be applied to manually.
    """
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise click.ClickException(f"{csv_path} is empty")
    header = [c.strip() for c in rows[0]]
    if header != _CSV_HEADER:
        raise click.ClickException(
            f"unexpected CSV header.\n  expected: {_CSV_HEADER}\n  got:      {header}"
        )
    data = rows[1:]

    db.init_db()  # standalone command — ensure the schema exists (idempotent)
    auto_resume_cap = int(os.environ.get("AUTO_RESUME_CAP_PER_CYCLE", "5"))
    new_count = 0
    dup_count = 0
    tier_counts: dict[str, int] = {}
    auto_resumes = 0
    skipped: list[tuple[str, str, str, str, str]] = []

    url_rows = [r for r in data if len(r) > _COL_URL and r[_COL_URL].strip()]
    seen = 0
    for r in data:
        num = r[_COL_NUM].strip() if len(r) > _COL_NUM else "?"
        title = r[_COL_TITLE].strip() if len(r) > _COL_TITLE else ""
        company = r[_COL_COMPANY].strip() if len(r) > _COL_COMPANY else ""
        url = r[_COL_URL].strip() if len(r) > _COL_URL else ""

        if not url or url.lower() == "open" or not url.startswith(("http://", "https://")):
            skipped.append((num, title, company, url or "Open", "no-url"))
            continue

        if limit and seen >= limit:
            break
        seen += 1
        console.print(f"[dim][{seen}/{len(url_rows) if not limit else limit}][/] {url}")

        job, status = inject_from_url(url)
        if not job:
            skipped.append((num, title, company, url, status))
            continue

        if not db.upsert_job(job):
            dup_count += 1
            continue

        new_count += 1
        res = enrich_scored(
            job_hash=job.hash,
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description or "",
        )
        if res["scored"]:
            tier_counts[res["tier"] or "?"] = tier_counts.get(res["tier"] or "?", 0) + 1
            if (
                res["tier"] == "strong"
                and (res["total"] or 0) >= 80
                and auto_resumes < auto_resume_cap
            ):
                path = autogen_resume_if_missing(
                    job.title, job.company, job.description or "", location=job.location
                )
                if path:
                    auto_resumes += 1
                    autogen_cover_letter_if_missing(
                        job.title, job.company, job.description or "", location=job.location
                    )

    summary = Table(title="inject-csv summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", justify="right")
    summary.add_row("Imported (new)", str(new_count))
    summary.add_row("Duplicates (already in DB)", str(dup_count))
    summary.add_row("Skipped (no URL / fetch failed)", str(len(skipped)))
    for t in ("strong", "possible", "stretch", "skip"):
        if tier_counts.get(t):
            summary.add_row(f"Scored {t}", str(tier_counts[t]))
    summary.add_row("Resumes + cover letters generated", str(auto_resumes))
    console.print(summary)

    if skipped:
        st = Table(title=f"{len(skipped)} skipped — apply to these manually")
        st.add_column("#", justify="right")
        st.add_column("Title")
        st.add_column("Company")
        st.add_column("URL")
        st.add_column("Reason", style="yellow")
        for num, title, company, url, reason in skipped:
            st.add_row(num, title[:45], company[:25], url[:60], reason)
        console.print(st)

        out = csv_path.with_name(f"{csv_path.stem}_skipped.csv")
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["#", "Title", "Company", "Apply URL", "Reason"])
            w.writerows(skipped)
        console.print(f"[green]wrote skipped rows ->[/] {out}")


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
    table.add_row(
        "Possible (60-79)", str(int(((df["score_total"] >= 60) & (df["score_total"] < 80)).sum()))
    )
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
                str(int(row["score_total"])),
                str(row["tier"]),
                str(row["title"])[:50],
                str(row["company"])[:30],
                str(row["source"]),
            )
        console.print(t2)


if __name__ == "__main__":
    cli()
