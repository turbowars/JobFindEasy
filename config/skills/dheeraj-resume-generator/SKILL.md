---
name: dheeraj-resume-generator
description: "Generate ATS-compliant, JD-tailored resumes for Dheeraj Sampath across IC (Staff/Principal/Tech Lead/Full Stack/Product Engineer) and EM (Engineering Manager/Director) tracks. Use whenever Dheeraj asks for a resume, CV, to tailor his resume to a job, to customize his resume for an application, to optimize a resume for ATS, to pick a resume title or headline, or mentions any resume generation task. Trigger on phrases like 'resume for this role', 'tailor my resume', 'ATS resume', 'customize my CV', 'build me a resume', 'which resume should I use', 'what title should I use', or any job application resume request. Always use this skill — never produce a generic resume for Dheeraj."
---

# Dheeraj's ATS Resume Generator

Tailored resume generation for modern ATS parsers (Workday, Greenhouse, Ashby, Lever, Taleo, iCIMS). Every resume is built for a specific JD. Picks the right base profile (IC or EM), mirrors the JD title exactly, and produces a `.docx` that parses cleanly.

---

## Writing Style Rules (apply to every output artifact)

These apply to the `.docx` Dheeraj will send. They do NOT apply to this skill file or chat responses.

- **No em dashes (—).** Ever.
- **No en dashes (–) in prose.** Exception: date ranges ("Aug 2022 – Present") are fine.
- Replace dashes with a period, comma, colon, parentheses, or semicolon, whichever reads cleanest.
- Smart quotes (' ") for apostrophes are fine.
- **Final pass:** grep the output text for `—` and `–` in prose and rewrite any hits before delivery.

---

## Mandatory Output Rules (HARD, apply to every artifact)

These rules supersede any conflicting instruction in later sections.

1. **No track tags in visible output.** Never write `(IC)` or `(EM)` anywhere in the generated resume — not in the headline, summary, role title, bullet, skill, or anywhere else. Track is internal scaffolding only.

2. **EM headline = ONE slot.** When generating an EM-track resume, the headline contains exactly one role title (the JD's target title, verbatim) — no pipe separators, no alternates. IC track keeps the 3-slot format.

3. **Mirror title hygiene.** A mirror role title (the parenthetical at Equifax, or any role's emitted title) MUST NOT contain the target company's name or a team/product name unique to the target company. Example WRONG: applying to Coinbase for "Engineering Manager, Legend" → DO NOT emit "Engineering Manager, Legend" at Equifax (Legend is Coinbase's NFT product). RIGHT: emit "Engineering Manager" or "Engineering Manager, Frontend Platform" — keep only generic scope qualifiers that describe transferable responsibility.

4. **Canonical company names.** When listing Dheeraj's previous roles, always use these exact spellings — never include legal-entity suffixes or alternate forms:
   - `nowfloats` (lowercase, no "Technologies", no "Ltd.")
   - `Equifax`
   - `Midigator`
   - `TA Digital`
   - `Deloitte Digital Studio`
   - `Neudesic`

---

## Required Inputs

Before generating, confirm:

1. **Target JD** — a URL (fetch with web_fetch), pasted JD text, or at minimum the exact job title + company name.
2. **Target title** — the literal title string from the JD headline to mirror.
3. **Company name** — for the filename.

If any are missing, ask. This skill does NOT produce a blank "master" resume. It is always tailored.

---

## Step 0: Pick the Base Profile (Track Detection)

Every resume starts here. Read the JD's target title and pick one of two base profiles:

### IC Profile → use `references/profile-ic.md`

Use when the JD target title is any of:
- Any Frontend Engineer variant (Frontend Engineer, Staff Frontend, Senior Staff Frontend, Principal Frontend)
- Any Product Engineer variant (Product Engineer, Staff Product, Senior Staff Product, Principal Product)
- Any Full Stack Engineer variant (Full Stack, Senior Full Stack, Staff Full Stack, Principal Full Stack)
- Any Tech Lead variant (Frontend TL, Full Stack TL, Software Engineer TL, Staff/Principal TL)
- Software Engineer (flat-title shops only, e.g., Airbnb, Figma, Stripe, Linear)
- Web Platform Engineer

**Contact:** Austin, TX | 512-843-2081 | dheeraj26engineer@gmail.com | linkedin.com/in/evolvingdx | dheerajsampath.com
**Years:** 15+

### EM Profile → use `references/profile-em.md`

Use when the JD target title is any of:
- Engineering Manager (with or without domain suffix: Product / Web Platform / Growth / CX)
- Software Engineering Manager
- Frontend Engineering Manager
- Full Stack Engineering Manager
- Product Engineering Manager
- Web Platform Engineering Manager
- Growth Engineering Manager
- Director of Engineering, VP of Engineering, Head of Engineering

**Contact:** Austin, TX | 248-873-8929 | dheerajsampath@proton.me | linkedin.com/in/evolvingdx | dheerajsampath.framer.website
**Years:** 15+

### Ambiguity Rules

- **Hybrid EM/IC roles ("Staff EM," "Player-Coach," "Lead Engineer who manages 2"):** use EM profile. Elevate IC bullets inside it.
- **Unclear from the JD:** ask Dheeraj before generating.
- **Never mix profiles in one resume.** Contact info, email, phone, years — all come from one profile.

---

## Step 1: Pick the Headline

**Rule:** Slot 1 = JD target title, exact string, exact capitalization. Slots 2 and 3 = adjacent range within the same track. Never mix tracks.

Pipe separator with two spaces on each side: `Title  |  Title  |  Title`.

### IC — Frontend Track

| JD target | Headline |
|---|---|
| Frontend Engineer | `Frontend Engineer  \|  Senior Frontend Engineer  \|  Frontend Tech Lead` |
| Staff Frontend Engineer | `Staff Frontend Engineer  \|  Senior Staff Frontend Engineer  \|  Frontend Tech Lead` |
| Senior Staff Frontend Engineer | `Senior Staff Frontend Engineer  \|  Staff Product Engineer  \|  Principal Frontend Engineer` |
| Principal Frontend Engineer | `Principal Frontend Engineer  \|  Senior Staff Frontend Engineer  \|  Frontend Platform Architect` |

### IC — Product Engineer Track

| JD target | Headline |
|---|---|
| Product Engineer | `Product Engineer  \|  Senior Product Engineer  \|  Full Stack Engineer` |
| Staff Product Engineer | `Staff Product Engineer  \|  Senior Staff Product Engineer  \|  Principal Full Stack Engineer` |
| Senior Staff Product Engineer | `Senior Staff Product Engineer  \|  Principal Product Engineer  \|  Staff Full Stack Engineer` |
| Principal Product Engineer | `Principal Product Engineer  \|  Senior Staff Product Engineer  \|  Staff Full Stack Engineer` |

### IC — Full Stack Track (triggers re-weighting, see Step 2.5)

| JD target | Headline |
|---|---|
| Full Stack Engineer | `Full Stack Engineer  \|  Senior Full Stack Engineer  \|  Product Engineer` |
| Senior Full Stack Engineer | `Senior Full Stack Engineer  \|  Staff Full Stack Engineer  \|  Staff Product Engineer` |
| Staff Full Stack Engineer | `Staff Full Stack Engineer  \|  Senior Staff Full Stack Engineer  \|  Staff Product Engineer` |
| Principal Full Stack Engineer | `Principal Full Stack Engineer  \|  Staff Full Stack Engineer  \|  Staff Product Engineer` |

### IC — Generic / Platform

| JD target | Headline |
|---|---|
| Software Engineer (flat-title shops only) | `Software Engineer  \|  Senior Software Engineer  \|  Tech Lead, Frontend Platform` |
| Web Platform Engineer | `Web Platform Engineer  \|  Staff Frontend Engineer  \|  Frontend Platform Tech Lead` |

### IC — Tech Lead Track

Dheeraj's Equifax line literally reads "Tech Lead, Frontend Platform." This is his most truthful track for any Lead role.

| JD target | Headline |
|---|---|
| Front End Tech Lead / Frontend Tech Lead | `Front End Tech Lead  \|  Senior Staff Frontend Engineer  \|  Principal Frontend Engineer` |
| Staff Frontend Tech Lead | `Staff Frontend Tech Lead  \|  Senior Staff Frontend Engineer  \|  Principal Frontend Engineer` |
| Principal Frontend Tech Lead | `Principal Frontend Tech Lead  \|  Principal Frontend Engineer  \|  Staff Frontend Engineer` |
| Full Stack Tech Lead | `Full Stack Tech Lead  \|  Staff Full Stack Engineer  \|  Principal Full Stack Engineer` |
| Staff Full Stack Tech Lead | `Staff Full Stack Tech Lead  \|  Staff Product Engineer  \|  Principal Full Stack Engineer` |
| Principal Full Stack Tech Lead | `Principal Full Stack Tech Lead  \|  Principal Full Stack Engineer  \|  Staff Product Engineer` |
| Software Engineer Tech Lead / Software Tech Lead | `Software Engineer Tech Lead  \|  Staff Product Engineer  \|  Principal Full Stack Engineer` |

### EM Track

**EM headlines are exactly ONE slot.** Use the JD's target title verbatim, with no pipe separators, no alternates, no parentheticals. Strip out any embedded target-company name first (see "Mirror title hygiene" below).

| JD target | Headline |
|---|---|
| Engineering Manager | `Engineering Manager` |
| Software Engineering Manager | `Software Engineering Manager` |
| Frontend Engineering Manager | `Frontend Engineering Manager` |
| Full Stack Engineering Manager | `Full Stack Engineering Manager` |
| Product Engineering Manager | `Product Engineering Manager` |
| Web Platform Engineering Manager | `Web Platform Engineering Manager` |
| Growth Engineering Manager | `Growth Engineering Manager` |
| Director of Engineering | `Director of Engineering` |
| Engineering Manager, Identity Frontend | `Engineering Manager, Identity Frontend` *(generic scope qualifier OK)* |
| Engineering Manager, Legend (Coinbase team name) | `Engineering Manager` *(strip target-company team/product name)* |

### Pitfalls (do NOT do these)

- Leading with "Engineering Manager" on an IC resume → reads as burnt-out manager escaping to IC.
- Leading with "Senior Staff Frontend Engineer" on an EM resume → reads as IC who doesn't want to manage.
- Using "Principal Full Stack" when the JD is pure frontend → dilutes the FE match.
- Running all three slots as the same keyword → wastes slots 2 and 3.
- Mixing tracks inside a single headline.

---

## Step 2: Parse the JD and Build the Summary

### 2a: Extract from the JD

- **Literal job title** (for headline slot 1 and the Equifax title parenthetical).
- **Top 3 responsibilities** (usually the first 3 items under "What you'll do" / "Responsibilities").
- **Top 10 to 15 keywords**, weighted toward:
  - Tools / frameworks explicitly named (React, TypeScript, Next.js, Module Federation, GraphQL, Go, Python, AWS, etc.)
  - Scope / scale phrasing ("team of 10", "micro-frontend platform", "10M+ users")
  - Seniority phrasing ("technical direction", "load-bearing code", "RFC", "hiring", "people management")
  - AI signals (Anthropic Claude, OpenAI, MCP, RAG, vector DB, LLM, agents)
- **Dealbreakers** — sponsorship stance, location, comp floor. Flag conflicts up front.
- **Nice-to-haves** — specific domains (fintech, consumer, edtech, security), leadership frameworks.

Show the extracted list to Dheeraj before generating so he can add missing signals or correct priority.

### 2b: Summary-line guardrail (HARD RULE)

The **noun phrase leading the headline slot 1** must match the opening of the Professional Summary.

| Headline slot 1 leads with | Summary opens with |
|---|---|
| Principal Frontend Engineer | *"Principal frontend engineer with 15+ years..."* |
| Staff Product Engineer | *"Staff product engineer with 15+ years..."* |
| Senior Staff Frontend Engineer | *"Senior Staff / Principal frontend engineer with 15+ years..."* |
| Front End Tech Lead | *"Frontend tech lead with 15+ years..."* |
| Principal Full Stack Engineer | *"Principal full stack engineer with 15+ years..."* |
| Engineering Manager, Growth | *"Engineering manager with 15+ years leading growth and product engineering teams..."* |
| Frontend Engineering Manager | *"Engineering manager with 15+ years leading frontend platform and product teams..."* |

If these don't match, the resume reads mis-targeted even if every bullet is perfect.

### 2c: Summary template (3 to 5 lines)

```
[Slot-1 noun phrase] with 15+ years [JD's core domain, phrased to match]. [Scope achievement aligned to JD priority #1]. [Stack line aligned to JD's named tools]. [Leadership / culture signal pulled from JD language]. H-1B with approved I-140 and AC21 portability.
```

Always include the I-140 / AC21 line for both profiles. It's a major sponsorship-friendly signal and belongs in the first 80 words.

---

## Step 2.5: Full Stack Re-weighting (Full Stack track only)

When the base profile is IC AND the track is Full Stack (any variant), re-order the Skills section so backend and infra appear BEFORE frontend:

Default IC Skills order:
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
12. ...

Full Stack re-weighted order:
1. Languages (emphasize TypeScript, Go, Python, Node.js)
2. **Backend / APIs / Integration** (moved up)
3. **Databases** (moved up)
4. **AWS Cloud** (moved up)
5. **GCP Cloud** (moved up)
6. Frontend Frameworks & Libraries
7. Micro-Frontend / Platform
8. UI / Styling / Design Systems
9. Build Tools
10. Edge / Serverless
11. AI / LLM Engineering
12. ...

Also: for the Equifax bullets, lead with the BFF/GraphQL, IDP (Go+Node.js), and event-pipeline bullets rather than the Module Federation bullet. The frontend stuff stays on the resume, just not in the top 3.

---

## Step 3: Select and Rewrite Experience Bullets

Load the chosen profile's reference file (`references/profile-ic.md` or `references/profile-em.md`) for the full bullet pool.

**Per-role bullet budget (IC profile):**
- Equifax: 6 to 8 bullets (most recent + highest-impact)
- Midigator: 3 to 5 bullets
- TA Digital USA: 4 to 6 bullets (split across Strategic Education + AARP if space allows)
- TA Digital India: 3 to 4 bullets (Fitbit or Bose, pick whichever maps to the JD)
- NowFloats: 2 to 3 bullets
- Deloitte: 2 bullets (usually just title line + 1 to 2 for older roles)
- Neudesic: 1 bullet or title line only

**Per-role bullet budget (EM profile):**
- Equifax: 4 to 6 bullets
- Midigator: 3 to 4 bullets
- TA Digital: 2 to 3 bullets
- NowFloats: 1 bullet
- Deloitte, Neudesic: title line only

**Rewrite rules:**
- Keep the numeric metric intact. Rotate the verb and framing.
- Rewrite the top 2 to 3 bullets of the most recent role (Equifax) to mirror the JD's top 3 priorities.
- For AI-heavy JDs, elevate the LLM code-review agent, MCP server, and RAG bullets at Equifax.
- For platform-heavy JDs, elevate Module Federation, IDP (internal developer platform), and BFF bullets.
- For product-heavy JDs, elevate Next.js 14 migration, LCP/TTI improvements, and user-facing feature bullets.

---

## Step 4: Build the Skills Section

Flat list grouped by category (not nested). Reorder so categories prominent in the JD appear first. Drop any category the JD does not care about. Add new keywords lifted directly from the JD if the candidate has the experience.

See the full Skills block in the chosen profile reference file. Trim to roughly 10 to 14 category lines for the final resume.

---

## Step 5: Generate the .docx

Use the `docx` skill (`/mnt/skills/public/docx/SKILL.md`) to produce the file.

### Formatting (non-negotiable)

- **Single column only.** No two-column, sidebar, or multi-region layouts.
- **No tables, text boxes, headers/footers, graphics, icons, or images.** Contact info in the document body as plain text.
- **Margins:** 0.7" all sides.
- **Font:** Calibri 11pt body, 12pt section headers, 14pt name. Arial, Helvetica, Georgia, or Times are acceptable alternatives.
- **Line spacing:** 1.0. Space after paragraph: 6pt.
- **Bullets:** standard `•` via `LevelFormat.BULLET` (never hardcoded Unicode). 0.2" indent.

### Section headers (exact strings)

- `Professional Summary`
- `Core Technical Skills` (or `Skills`)
- `Professional Experience` (or `Experience`)
- `Selected Projects` (optional, IC profile only)
- `Education & Certifications`

### Dates

Format: `MMM YYYY – MMM YYYY` (e.g., `Aug 2022 – Present`). The en dash inside date ranges is allowed. This is the only exception.

### Equifax title line (title mirror)

Apply to the Equifax line only. Format:
```
[JD target title] (Tech Lead, Frontend Platform — IC Staff scope)
```
For example, if the JD is "Staff Product Engineer":
```
Staff Product Engineer (Tech Lead, Frontend Platform — IC Staff scope)
```

For EM profile, use:
```
[JD target title] (Engineering Lead, Frontend Platform)
```

### Filename

`Dheeraj_Sampath_[Target_Title]_[Company].docx` using underscores. Example: `Dheeraj_Sampath_Principal_Frontend_Engineer_Vercel.docx`.

### Save location

`/mnt/user-data/outputs/`

---

## Step 6: Pre-submit Checklist

Before presenting the file, verify:

- [ ] Grep the text for `—` and `–` in prose. Rewrite any hits. Date ranges only allowed exception.
- [ ] Headline slot 1 = exact JD target title.
- [ ] Summary line 1 noun phrase matches headline slot 1.
- [ ] JD keyword match rate: count how many of the top 10 JD keywords appear in the resume. Target 70% or higher.
- [ ] Every bullet has a number, percentage, scope, or named tool.
- [ ] Section headers use the exact strings listed above.
- [ ] Filename follows convention.
- [ ] Contact line in document body, not in a Word header.
- [ ] Correct profile used (IC contact vs EM contact, never mixed).
- [ ] I-140 / AC21 line present in Summary.
- [ ] If Full Stack track: Skills section re-weighted (backend before frontend).
- [ ] No tables, text boxes, graphics, or images anywhere.

---

## Output Format

Present to Dheeraj in this order:

### 1. The .docx file
Via `present_files`.

### 2. Tailoring Report

```
Tailoring Report
Profile used: [IC or EM]
Headline: [the 3-slot headline as generated]
Title Mirror (Equifax line): [the exact string]
Keyword Match: X/10 top JD keywords hit
Priorities Addressed:
  - JD priority 1: [→ which bullet covers it]
  - JD priority 2: [→ which bullet covers it]
  - JD priority 3: [→ which bullet covers it]
Missing Signals: [anything the JD wants that Dheeraj's background doesn't cover — flag for his call, do NOT fabricate]
```

### 3. Submission Reminder

```
Before submitting:
- Apply within 48h of posting (recruiter queues work newest-first)
- Check parsed-resume preview in Greenhouse or Workday. Fix if parsed wrong.
- Find hiring manager on LinkedIn and DM same day referencing the role
- Check for referral options before submitting cold
- Use [the email on the profile] (same as LinkedIn) for ATS-LinkedIn enrichment
```

---

## Quick Copy — Contact Lines

**IC Profile contact (3 lines, plain text in document body):**
```
Dheeraj Sampath
Austin, TX  |  512-843-2081  |  dheeraj26engineer@gmail.com
linkedin.com/in/evolvingdx  |  dheerajsampath.com
```

**EM Profile contact (3 lines, plain text in document body):**
```
Dheeraj Sampath
Austin, TX  |  248-873-8929  |  dheerajsampath@proton.me
linkedin.com/in/evolvingdx  |  dheerajsampath.framer.website  |  github.com/turbowars
```

Name on line 1 (14pt bold). Location/phone/email on line 2. Links on line 3. All in the document body, never in a Word header.

---

## Reference Files

- `references/profile-ic.md` — Full IC Staff/Principal/Tech Lead profile. Load when Step 0 selects IC.
- `references/profile-em.md` — Full EM/Director profile. Load when Step 0 selects EM.

---

## When NOT to Use This Skill

- LinkedIn About section or headline copy — different artifact.
- Cover letters — different artifact.
- JD scoring only, no resume build — use `dheeraj-job-search` instead.
- Generic "master resume" with no target role — this skill refuses. Ask for a JD.
