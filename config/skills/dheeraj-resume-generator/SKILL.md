---
name: dheeraj-resume-generator
description: "Generate ATS-compliant, JD-tailored resumes for Dheeraj Sampath. Use whenever Dheeraj asks for a resume, CV, to tailor his resume to a job, to customize his resume for an application, to optimize a resume for ATS, to pick a resume title or headline, or mentions any resume generation task. Trigger on phrases like 'resume for this role', 'tailor my resume', 'ATS resume', 'customize my CV', 'build me a resume', 'which resume should I use', 'what title should I use', or any job application resume request. Always use this skill — never produce a generic resume for Dheeraj."
---

# Dheeraj's ATS Resume Generator

Tailored resume generation for modern ATS parsers (Workday, Greenhouse, Ashby, Lever, Taleo, iCIMS). Every resume is built for a specific JD. Picks the right base profile, mirrors the JD title exactly, and produces a `.docx` that parses cleanly.

---

## Rules (apply to every output artifact)

- **No em dashes (—).** Ever. Replace with a period, comma, colon, semicolon, or parentheses.
- **No en dashes (–) in prose.** Exception: date ranges only (`Aug 2022 – Present`).
- **Canonical company names** — always use these exact spellings:
  - `Nowfloats Technologies Pvt Ltd`
  - `Equifax`
  - `Midigator`
  - `TA Digital`
  - `Deloitte Digital Studio`
  - `Neudesic`

---

## Required Inputs

Before generating, confirm:

1. **Target JD** — a URL (fetch with web_fetch), pasted JD text, or at minimum the exact job title + company name.
2. **Target title** — the literal job title string from the JD.
3. **Company name** — for the filename.

If any are missing, ask. This skill does NOT produce a blank "master" resume. It is always tailored.

---

## Step 0: Choose the Profile

**Management profile** — use when the JD title primarily involves managing people:

- Engineering Manager (any domain suffix: Product, Web Platform, Growth, Frontend, Full Stack, CX)
- Software Engineering Manager
- Director of Engineering, VP of Engineering, Head of Engineering
- Any player-coach or hybrid where headcount management is the primary responsibility

**Engineering profile** — use for all other titles:

- Any Frontend Engineer variant (Staff, Senior Staff, Principal)
- Any Product Engineer or Full Stack Engineer variant
- Any Tech Lead variant (Frontend TL, Full Stack TL, Staff/Principal TL)
- Software Engineer (flat-title shops: Airbnb, Figma, Stripe, Linear)
- Web Platform Engineer

When in doubt: if managing headcount is the primary job, use Management. If individual technical output is primary, use Engineering.

---

## Step 1: Parse the JD and Build the Summary

### 1a: Extract from the JD

- **Literal job title** — for the Equifax title parenthetical and the summary opening.
- **Top 3 responsibilities** — usually the first 3 items under "What you'll do" or "Responsibilities."
- **Top 10 to 15 keywords**, weighted toward:
  - Tools / frameworks explicitly named (React, TypeScript, Next.js, Module Federation, GraphQL, Go, Python, AWS, etc.)
  - Scope / scale phrasing ("team of 10", "micro-frontend platform", "10M+ users")
  - Seniority phrasing ("technical direction", "load-bearing code", "RFC", "hiring", "people management")
  - AI signals (Anthropic Claude, OpenAI, MCP, RAG, vector DB, LLM, agents)
- **Dealbreakers** — sponsorship stance, location, comp floor. Flag conflicts up front.
- **Nice-to-haves** — specific domains (fintech, consumer, edtech, security, healthcare, healthtech, wellness, health), leadership frameworks.

Show the extracted list to Dheeraj before generating so he can add missing signals or correct priority.

### 1b: Summary-line guardrail (HARD RULE)

The **noun phrase leading the JD target title** must match the opening of the Professional Summary.

| JD target title                | Summary opens with                                                                     |
| ------------------------------ | -------------------------------------------------------------------------------------- |
| Principal Frontend Engineer    | _"Principal frontend engineer with 15+ years..."_                                      |
| Staff Product Engineer         | _"Staff product engineer with 15+ years..."_                                           |
| Senior Staff Frontend Engineer | _"Senior Staff / Principal frontend engineer with 15+ years..."_                       |
| Front End Tech Lead            | _"Frontend tech lead with 15+ years..."_                                               |
| Principal Full Stack Engineer  | _"Principal full stack engineer with 15+ years..."_                                    |
| Engineering Manager, Growth    | _"Engineering manager with 15+ years leading growth and product engineering teams..."_ |
| Frontend Engineering Manager   | _"Engineering manager with 15+ years leading frontend platform and product teams..."_  |

If these don't match, the resume reads mis-targeted even if every bullet is perfect.

### 1c: Summary template (3 to 5 lines)

```
[JD target title noun phrase] with 15+ years [JD's core domain, phrased to match]. [Scope achievement aligned to JD priority #1]. [Stack line aligned to JD's named tools]. [Leadership / culture signal pulled from JD language].
```

---

## Step 2: Select and Rewrite Experience Bullets

Load `references/profile.md` and use the **Engineering track** or **Management track** bullet pool for the chosen profile.

**Engineering profile — bullet budgets per role:**

- Equifax: 6 to 8 bullets (most recent + highest-impact)
- Midigator: 3 to 5 bullets
- TA Digital USA: 4 to 6 bullets (split across Strategic Education + AARP if space allows)
- TA Digital India: 3 to 4 bullets (Fitbit or Bose, pick whichever maps to the JD)
- nowfloats: 2 to 3 bullets
- Deloitte Digital Studio: 2 bullets (title line + 1 to 2)
- Neudesic: 1 bullet or title line only

**Management profile — bullet budgets per role:**

- Equifax: 4 to 6 bullets
- Midigator: 3 to 4 bullets
- TA Digital: 2 to 3 bullets
- nowfloats: 1 bullet
- Deloitte Digital Studio, Neudesic: title line only

**Rewrite rules:**

- Keep every numeric metric intact. Rotate the verb and framing.
- Rewrite the top 2 to 3 Equifax bullets to mirror the JD's top 3 priorities.
- For AI-heavy JDs: elevate the LLM code-review agent, MCP server, and RAG bullets.
- For platform-heavy JDs: elevate Module Federation, IDP (internal developer platform), and BFF bullets.
- For product-heavy JDs: elevate Next.js 14 migration, LCP/TTI improvements, and user-facing feature bullets.

---

## Step 3: Build the Skills Section

Flat list grouped by category (not nested). Reorder so categories most prominent in the JD appear first. Drop any category the JD does not care about. Add keywords lifted directly from the JD if Dheeraj has the experience.

See the full Skills block in the chosen profile reference file. Trim to roughly 10 to 14 category lines.

### Full Stack re-weighting (Full Stack targets only)

When the JD target title is any Full Stack variant, reorder so backend and infra appear before frontend. Also lead the Equifax bullets with BFF/GraphQL, IDP (Go+Node.js), and event-pipeline work rather than Module Federation.

**Standard order:**

1. Languages
2. Frontend Frameworks & Libraries
3. Build Tools / Bundlers / Monorepos
4. UI / Styling / Design Systems
5. Micro-Frontend / Platform
6. Backend / APIs / Integration
7. Databases
8. AWS Cloud
9. GCP Cloud
10. Edge / Serverless
11. AI / LLM Engineering

**Full Stack re-weighted order:**

1. Languages (emphasize TypeScript, Go, Python, Node.js)
2. Backend / APIs / Integration
3. Databases
4. AWS Cloud
5. GCP Cloud
6. Frontend Frameworks & Libraries
7. Micro-Frontend / Platform
8. UI / Styling / Design Systems
9. Build Tools
10. Edge / Serverless
11. AI / LLM Engineering

---

## Step 4: Generate the .docx

Use the `docx` skill (`/mnt/skills/public/docx/SKILL.md`) to produce the file.

Follow `references/template.md` for document structure: what's locked verbatim, what gets generated, and bullet budgets per role.

### .docx formatting (non-negotiable)

- **Single column only.** No two-column, sidebar, or multi-region layouts.
- **No tables, text boxes, headers/footers, graphics, icons, or images.**
- **Margins:** 0.7" all sides.
- **Font:** Calibri 11pt body, 12pt section headers, 14pt name. Arial, Helvetica, Georgia, or Times are acceptable alternatives.
- **Line spacing:** 1.0. Space after paragraph: 6pt.
- **Bullets:** standard `•` via `LevelFormat.BULLET` (never hardcoded Unicode). 0.2" indent.
- **Contact block in document body, not in a Word header.** Name on line 1 (14pt bold). Location/phone/email on line 2. Links on line 3.
- **Dates:** `MMM YYYY – MMM YYYY` (e.g., `Aug 2022 – Present`). En dash in date ranges is the only exception to the no-en-dash rule.

### Filename

`Dheeraj_Sampath_[Target_Title]_[Company].docx` using underscores. Example: `Dheeraj_Sampath_Principal_Frontend_Engineer_Vercel.docx`.

### Save location

`/mnt/user-data/outputs/`

---

## Step 5: Pre-submit Checklist

Before presenting the file, verify:

- [ ] No `—` or `–` in prose. Date ranges are the only exception.
- [ ] Summary opens with the JD target title as the noun phrase.
- [ ] JD keyword match rate: at least 7 of the top 10 JD keywords appear in the resume.
- [ ] Every bullet has a number, percentage, scope, or named tool.
- [ ] Section headers match the exact strings above.
- [ ] Filename follows convention.
- [ ] Contact block is in the document body, not in a Word header.
- [ ] If Full Stack target: Skills section is re-weighted (backend before frontend).
- [ ] No tables, text boxes, graphics, or images anywhere.

---

## Output Format

Present to Dheeraj in this order:

### 1. The .docx file

Via `present_files`.

### 2. Tailoring Report

```
Tailoring Report
Profile used: [Engineering or Management]
Title Mirror (Equifax line): [exact string]
Keyword Match: X/10 top JD keywords hit
Priorities Addressed:
  - JD priority 1: [which bullet covers it]
  - JD priority 2: [which bullet covers it]
  - JD priority 3: [which bullet covers it]
Missing Signals: [anything the JD wants that Dheeraj's background doesn't cover — flag for his call, do NOT fabricate]
```

### 3. Submission Reminder

```
Before submitting:
- Apply within 48h of posting (recruiter queues work newest-first)
- Check parsed-resume preview in Greenhouse or Workday. Fix if parsed wrong.
- Find hiring manager on LinkedIn and DM same day referencing the role
- Check for referral options before submitting cold
- Use the email on the contact block for ATS-LinkedIn enrichment
```

---

## Reference Files

- `references/template.md` — Resume skeleton. Shows every locked field verbatim and every generated field as a labeled placeholder.
- `references/profile.md` — Bullet pool and Skills blocks for both tracks, plus contact, education, and Quick Copy answers.

---

## When NOT to Use This Skill

- Cover letters — different artifact.
- Generic "master resume" with no target role — this skill refuses. Ask for a JD.
