"""One-shot bulk-generate resumes for every strong-fit job that doesn't
already have one on disk. Runs sequentially in 3 worker threads — same cap
as the Streamlit UI executor — so you can keep using the app while it runs.
"""
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import db
from src.resume import generate_resume, existing_resume_path

MAX_WORKERS = 3


def _gen(job: dict) -> dict:
    title = job["title"]
    company = job["company"]
    location = job.get("location") or ""
    desc = job.get("description") or ""
    try:
        path, report = generate_resume(title, company, desc, location=location)
        scores = report.get("scores", {}) if report else {}
        return {
            "title": title, "company": company,
            "ok": True,
            "path": path.name,
            "ats": scores.get("ats_match", {}).get("match_pct"),
            "hr": scores.get("hr", {}).get("hr_score"),
            "retried": scores.get("retried"),
        }
    except Exception as e:
        return {"title": title, "company": company, "ok": False, "error": str(e)}


def main() -> int:
    df = db.to_dataframe()
    strong = df[(df["score_total"] >= 80) & (df["tier"] == "strong")].sort_values(
        "score_total", ascending=False
    )

    todo = []
    for _, r in strong.iterrows():
        path = existing_resume_path(r["title"], r["company"], r["location"] or "")
        if path.exists():
            print(f"  ✓ already have: {path.name}")
            continue
        todo.append(r.to_dict())

    if not todo:
        print("Nothing to do — every strong fit already has a resume.")
        return 0

    print(f"\nGenerating {len(todo)} resumes with {MAX_WORKERS} workers...")
    print()

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bulk-gen") as ex:
        futures = {ex.submit(_gen, j): j["title"] for j in todo}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if r.get("ok"):
                print(
                    f"  ✓ {r['title'][:55]:<55} @ {r['company']:<14} "
                    f"ATS={r['ats']} HR={r['hr']} retried={r['retried']}"
                )
            else:
                print(f"  ✗ {r['title']} @ {r['company']}: {r['error']}")

    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"\n{ok_count}/{len(results)} resumes generated successfully.")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
