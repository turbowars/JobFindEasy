"""Claude-for-Chrome prompt for the application automation loop."""

from __future__ import annotations


def build_claude_prompt(
    application_defaults: dict,
    *,
    base_url: str = "http://127.0.0.1:8826",
    batch_size: int = 5,
) -> str:
    """Return the self-contained prompt pasted into Claude-for-Chrome."""
    appl = application_defaults
    lines: list[str] = []

    lines.append("You are operating inside Dheeraj's personal job-hunt dashboard.")
    lines.append(f"Dashboard URL: {base_url}  (keep this tab open - never navigate it away)")
    lines.append("")
    lines.append(
        "Your job is to apply to every role in the dashboard's actionable "
        f"queue, top-down, BATCHING {batch_size} JOBS AT A TIME. Within each "
        f"batch you prep all {batch_size} silently using the JSON APIs below, "
        "then alert Dheeraj with a compact summary and wait for ONE combined "
        '"go ahead" before submitting all 5. Do not pause between individual '
        "jobs during prep."
    )
    lines.append("")

    lines.append("## API contract")
    lines.append("")
    lines.append(
        "All per-job context comes from JSON. Use `javascript_tool` to "
        f"`fetch()` these endpoints (they're served by the dashboard at {base_url}):"
    )
    lines.append("")
    lines.append(
        f"- **`GET {base_url}/api/apply/next`** -> returns the next job only when resume + cover letter already exist. If the top queued job is missing artifacts, it queues generation, marks the row `blocked_missing_artifacts`, and returns no job."
    )
    lines.append(
        f"- **`GET {base_url}/api/queue.json`** -> `{{queue: [{{hash, title, company, score, tier, status, apply_url}}, ...], total, returned}}`. The actionable queue, ordered tier -> status -> score."
    )
    lines.append(
        f'- **`GET {base_url}/api/job/<hash>.json`** -> full per-job bundle: `title`, `company`, `description` (full JD text), `url` (apply URL), `track` (em|ic), `artifacts.resume_path` + `cover_letter_path` (absolute paths on disk - pass directly to `file_upload`), `cover_letter_content.why_this_company` (use VERBATIM for "why this company?" form fields; null means leave blank + flag), `application_defaults` (every personal fact you need), `actions.mark_applied` + `mark_no_sponsorship` (POST URLs).'
    )
    lines.append(
        f'- **`POST {base_url}/api/applied/<hash>`** -> marks the row as Applied. Returns `{{ok: true, status: "applied"}}`.'
    )
    lines.append(
        f'- **`POST {base_url}/api/no-sponsorship/<hash>`** -> marks the row as No Sponsorship. Returns `{{ok: true, status: "no_sponsorship"}}`.'
    )
    lines.append("")
    lines.append(
        "Read `application_defaults` from each job's JSON for personal facts "
        "(email, phone, LinkedIn, GitHub, location, pronouns, citizenship, "
        "work authorization, comp expectation, etc.). It's the single source "
        "of truth - never hard-code these from anywhere else."
    )
    lines.append("")

    lines.append("## Sponsorship rule")
    lines.append("")
    lines.append(f"- Dheeraj's stance: {appl['work_authorization']}")
    lines.append(
        "- If the JD explicitly DENIES sponsorship -> do NOT apply. "
        "POST `/api/no-sponsorship/<hash>` and move on."
    )
    lines.append(
        "- If the JD is SILENT on sponsorship -> apply anyway. Clarify in "
        "the form's free-text or recruiter follow-up if asked."
    )
    lines.append("")

    lines.append("## Tab discipline")
    lines.append("")
    lines.append(
        f"- **Dashboard tab** ({base_url}) - already open. The dashboard is the "
        "API host; stay subscribed. NEVER navigate this tab away."
    )
    lines.append(
        "- **Job tab** - a NEW tab per job. Always open the apply URL in a "
        "new tab (right-click -> Open in new tab)."
    )
    lines.append("")

    lines.append(f"## Workflow - process the queue in BATCHES OF {batch_size}")
    lines.append("")
    lines.append(f"**Per-job prep (repeat for each of the next {batch_size} jobs in the queue):**")
    lines.append("")
    lines.append(
        f"1. **Pop the next ready job.** `fetch('{base_url}/api/apply/next')`. "
        "If `job` is null and `blocked_missing_artifacts` is true, the dashboard "
        "queued generation for that job; immediately call the endpoint again for "
        "the next ready job. If `queue_empty` is true, jump to the End-of-queue summary."
    )
    lines.append(
        f"2. **Fetch the bundle if needed.** `/api/apply/next` already returns the same bundle as `fetch('{base_url}/api/job/<hash>.json')`; use the bundle's `description`, `application_defaults`, `cover_letter_content.why_this_company`, `artifacts.resume_path`, and `artifacts.cover_letter_path`."
    )
    lines.append(
        "3. **Sponsorship check.** Scan `description` for explicit denial "
        'language (e.g. "unable to sponsor", "no visa sponsorship"). '
        "If found, POST `actions.mark_no_sponsorship` and skip to the next "
        "queue entry - do NOT continue to step 4."
    )
    lines.append(
        "4. **Open the apply URL** in a NEW tab (`actions.open_apply_url` "
        "or equivalently the JSON's `url` field). Wait for the Simplify "
        "Chrome extension to autofill (~5-10s, sometimes longer)."
    )
    lines.append(
        "5. **Verify and fill.** Fix any wrong autofill answers using "
        '`application_defaults`. Fill remaining text fields. For "why '
        'this company / role?" use `cover_letter_content.why_this_company` '
        "VERBATIM (do not paraphrase or expand). For EEO questions, mark "
        '"Decline to self-identify" when offered. For anything not '
        "covered by `application_defaults` and not the why-this-company "
        "field, pick a safe best-guess (or leave blank if no safe guess) "
        "and **flag it for the batch callout** in step 7. Do not pause "
        "mid-prep to ask Dheeraj - flag and move on."
    )
    lines.append(
        "6. **Upload artifacts.** Use the `file_upload` tool with "
        "`paths=[artifacts.resume_path]` (and "
        "`[artifacts.cover_letter_path]` separately if the form has a "
        "cover-letter field) and the file-input element ref from "
        "`read_page` / `find`. Reading directly from `data/exports/` skips "
        "the Downloads-folder round-trip. If `file_upload` fails (some "
        "ATS forms wrap the input in custom widgets), fall back to "
        "drag-and-drop: name the exact filename "
        "(`artifacts.resume_filename`), wait for Dheeraj to drag it in, "
        "note in the batch summary which jobs needed the manual drag."
    )
    lines.append(
        "7. **Stop just before Submit.** Do NOT click submit. Move to "
        "the next queue entry. The form stays in its filled state; "
        "Dheeraj reviews + you submit during step 8."
    )
    lines.append("")
    lines.append(f"**Batch boundary (after {batch_size} jobs are prepped):**")
    lines.append("")
    lines.append("8. ALERT Dheeraj with this exact two-section summary:")
    lines.append("")
    lines.append("    ```")
    lines.append(f"    Batch <N>: {batch_size} jobs prepped, ready to submit")
    lines.append("    ----------------------------------------------------------")
    lines.append(
        "    [1] <Company> - <Title>   * resume yes * cover yes/-  * <K> free-text answers   * flagged: <thing or ->"
    )
    lines.append("    [2] ...")
    lines.append("    [3] ...")
    lines.append("    [4] ...")
    lines.append("    [5] ...")
    lines.append("")
    lines.append("    Needs your eyes")
    lines.append("    ---------------")
    lines.append("    [1] <field name>: <best-guess Claude filled> - <why uncertain>")
    lines.append("    [2] <field name>: blank - <reason>")
    lines.append("    (omit this section if no flags)")
    lines.append("    ```")
    lines.append("")
    lines.append(
        '    Wait for an explicit "go ahead" reply (or per-job overrides '
        'like "go on 1, 2, 4 - skip 3 and 5"). NEVER click Submit without '
        "that confirmation."
    )
    lines.append("")
    lines.append(
        "9. **Submit + mark applied.** For each approved job: click "
        "Submit on the job tab, then "
        "`fetch(actions.mark_applied, {method: 'POST'})` from the "
        "dashboard tab."
    )
    lines.append("")
    lines.append("10. Loop back to step 1 for the next batch.")
    lines.append("")

    lines.append("## End-of-queue summary (after the final batch)")
    lines.append("")
    lines.append("Post a single message containing:")
    lines.append("")
    lines.append("- **Total submitted** - count.")
    lines.append(
        "- **Total skipped + reasons** - grouped by reason (sponsorship-denied, form-broken, missing-data, manual-skip)."
    )
    lines.append("- **Total time spent** - elapsed wall-clock from batch 1 start.")
    lines.append(
        "- **Follow-up companies** - any job where Claude flagged something Dheeraj should personally revisit (e.g. recruiter contact left in the form, unusual question, take-home assignment)."
    )
    lines.append(
        "- **Questions Claude had to guess on** - a deduplicated list of "
        'every form question that wasn\'t covered by the "About Dheeraj" '
        "context. This list feeds back into `src/resume/profile.py` "
        "`APPLICATION_DEFAULTS` for the next session - Dheeraj will add "
        "answers there so future batches don't need to flag them again."
    )
    lines.append("")

    lines.append("## Hard rules")
    lines.append(
        '- NEVER submit any job without Dheeraj\'s explicit "go ahead" at the batch boundary.'
    )
    lines.append(f"- NEVER navigate the dashboard tab away from {base_url}.")
    lines.append("- NEVER invent personal data. Best-guess + flag is OK; fabrication is not.")
    lines.append(
        "- If a JD explicitly DENIES sponsorship, set the row's status to **No Sponsorship** and do NOT apply."
    )
    lines.append(
        "- If a JD is SILENT on sponsorship, apply anyway (Dheeraj's stance: clarify in the form's free-text or recruiter follow-up)."
    )
    lines.append(
        '- Do not announce "starting job N" or "starting batch N" between jobs. Just keep moving.'
    )
    lines.append("")

    lines.append("## Start now")
    lines.append("")
    lines.append(
        "Click **Target** in the dashboard sidebar. The top ready rows are batch 1. "
        "Start at Workflow Step 1."
    )

    return "\n".join(lines)
