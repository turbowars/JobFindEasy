# Cover Letter Generation Skill — Dheeraj Sampath

## Purpose

A reusable skill for generating cover letters tailored to specific job applications. Each role family has its own master template that frames the same career history through the right lens for that audience.

## Core principles

1. **Frame matters more than facts.** The same Fitbit project can read as architect work, EM work, or program management work depending on which verbs lead and which numbers anchor the bullets. Pick the frame first, then choose evidence.
2. **No em dashes.** Use commas, colons, semicolons, or parentheses instead. This applies to every output, every time.
3. **Direct, punchy, short sentences.** No hedging, no filler, no generic praise.
4. **Skip rather than pad.** If a hook, a "why this company" line, or a bullet would feel like filler, leave it out.
5. **Visa status stays out.** Cover letters are the wrong surface; address it in the recruiter screen.
6. **No "free sample" offers.** That gesture reads as junior at senior engineering levels. If a hands-on signal is needed, offer a 30-minute architecture conversation or point to a public repo instead.

## Role family: Engineering Manager / Software Engineering Manager

### When to use

- Engineering Manager roles
- Senior Engineering Manager roles
- Software Engineering Manager roles
- Any role where the hiring intent is people leadership and end-to-end team ownership rather than IC technical depth

### Frame check (run before drafting)

Real job posts are messy. A "Senior Engineering Manager" role might be 70% IC work in disguise; a "Tech Lead Manager" role straddles both frames. Scan the JD before picking a template:

- **Count people-leadership signals:** hiring, performance management, 1:1s, growth, headcount, roadmap ownership, cross-functional partnership, stakeholder communication.
- **Count IC signals:** system design, architecture, code review, on-call, technical RFCs, hands-on coding expectations, specific framework or language requirements.

Decision rule:

- **People signals dominate (≥2x IC signals)** → use the standard EM template below.
- **Roughly balanced** → use the EM template but swap the credentials sentence for the **hybrid opening** (see below). Most modern EM roles at growth-stage companies land here.
- **IC signals dominate** → flag this to the user before drafting. The EM template will undersell the technical signal; consider the Staff IC template instead, or a hybrid lead.

**Hybrid opening (replaces the standard credentials sentence):**

> I'm a hands-on engineering manager with 15+ years across frontend and full-stack. I spend roughly 40% of my time in code (architecture, hard problems, code review) and 60% leading the team that ships it. At Equifax I lead the frontend platform group; before that I led delivery at Midigator (acquired by Equifax) and ran cross-geo engineering teams at TA Digital across engagements with Bose, Fitbit, AARP, and Strategic Education.

### Master template

```
Dear [Hiring Manager Name],

[Opening hook, pick one:
- [Name] suggested I reach out about your [Role Title] opening.
- I came across the [Role Title] role through [specific source].
- I'm applying for the [Role Title] role you posted on [source].]

[One specific thing about the team or company that drew you in: a product bet, a recent launch, an engineering principle they've published. Skip if you can't find something concrete.]

I'm an engineering manager with 15+ years leading frontend and full-stack teams that own products end to end, from design partnership through development, QA, and production operations. At Equifax I lead the frontend platform group; before that I led delivery at Midigator (acquired by Equifax) and ran cross-geo engineering teams at TA Digital across engagements with Bose, Fitbit, AARP, and Strategic Education.

A few things from my track record that line up with what you're hiring for:

- [Bullet 1, picked via JD signal matching]
- [Bullet 2, picked via JD signal matching]
- [Bullet 3, picked via JD signal matching]

[Optional: one line on why this company. Skip if it reads as filler.]

Resume attached. More at dheerajsampath.com and linkedin.com/in/evolvingdx. Happy to find time for a call.

Thanks,
Dheeraj Sampath
```

### Bullet selection: JD-signal matching

The cover letter uses three bullets. Do not default to the same three every time. Read the JD, identify its top three signals from the table below, and pick the matching bullet for each. If the JD only surfaces two clear signals, pick the third from the strongest unused signal.

**Lookup table:**

| JD signal (look for these phrases) | Best-fit bullet (and source evidence) |
|---|---|
| "Turn around," "rescue," "stuck," "complex stakeholders," "drive clarity" | **Fitbit rescue:** Took over a year-long stalled redesign as Scrum Master and Tech Lead. Established sprint cadence, rebuilt the client demo loop, set code review and QA standards, shipped to production with zero P0 or P1 defects across a 14-person cross-functional team. Fitbit extended the engagement on the back of that delivery. |
| "Scale a team," "grow the org," "hire," "headcount," "build out" | **Nowfloats Technologies Pvt Ltd scale:** Grew the engineering team from 5 to 12 without regressing velocity. Owned the hiring loop, technical interview design, and onboarding playbook; mentored two engineers into senior roles. |
| "Platform," "shared infrastructure," "developer experience," "internal tools," "design system" | **Equifax platform:** Lead the React and TypeScript micro-frontend platform powering the consumer product suite. 3 product teams ship against a shared component library and contract-tested design tokens; build cycles down 25%, cross-team release blockers eliminated. |
| "End-to-end," "production ownership," "on-call," "operational excellence," "reliability" | **Equifax E2E ownership:** My team owns its slice from design partnership through Playwright and Vitest gates in CI, OpenTelemetry and Core Web Vitals dashboards in production, and on-call rotations. Same pattern at Midigator: embeddable SDK design through partner integration testing and post-launch support. |
| "Cross-functional," "partner with PM and design," "stakeholder," "ambiguity" | **Strayer multi-brand:** Led a four-engineer cross-geo team for two years on the Strategic Education redesign and shared component system across Strayer and Sophia. Operated as the onshore technical bridge across product, design, analytics, and offshore engineering; ran architecture reviews, pairing, and client-facing demos. |
| "Quality," "testing culture," "reduce defects," "raise the bar" | **Bose authentication:** Led the front-end authentication layer integrated end-to-end with Okta. Established testing patterns (unit, integration, defensive error handling around every API boundary, polished UI error states for every failure mode) that became the delivery standard across every subsequent engagement. |
| "Performance," "Core Web Vitals," "user experience," "speed" | **Strayer performance program:** Drove a Lighthouse-based performance program that lifted scores from the 40s into the 90s, with image optimization alone improving page load by approximately 80%. Brought the site comfortably into Core Web Vitals "Good" thresholds. |
| "Mentor," "grow engineers," "develop," "career growth" | **Equifax mentoring:** Mentor senior and staff-track ICs through pairing, architectural coaching, and code review. Author the RFCs and run the design reviews that shape platform direction across 3 teams. |
| "AI," "LLM," "AI-augmented," "AI tooling" (EM context) | **Equifax AI tooling rollout:** Shipped an LLM-powered code-review assistant (Claude API plus AST analysis) adopted by 40+ engineers, reducing post-merge defects roughly 30%. Owned the rollout: scoping, integration, adoption, feedback loops. |

**Selection rules:**

- Three bullets, three different signals. No double-dipping (e.g., don't pick two Equifax bullets unless the JD has two distinct signals that both map there).
- If the JD has a strong people-leadership emphasis, at least one bullet must be from the people/culture row (Nowfloats Technologies Pvt Ltd scale, Equifax mentoring).
- If a JD signal isn't in the table, write the bullet using the closest available evidence and flag it to the user.

### Frame discipline checklist

Before sending, scan for these IC-coded patterns and rewrite if found:

- Bullets leading with "architected," "built," or "designed" → swap to "led the team that," "ran delivery on," or "owned end to end"
- Specific framework or library names dominating a bullet → demote to a clause; the team and outcome should lead
- Module Federation, AI / LLM specifics, AST analysis → keep these for IC Staff letters, not EM letters (exception: AI tooling bullet when the JD explicitly asks for it)
- Solo "I" verbs throughout → mix in "my team," "the engineers I led," "we shipped"

## Role family: Staff / Senior Staff / Principal IC

*Placeholder. Use the original frame: lead with technical scope, RFCs, hard problems solved, and bar-raising. Module Federation, AI tooling, and platform engineering specifics belong in the lead, not the supporting cast.*

## Workflow when generating a new cover letter

1. **Intake.** Confirm or ask for: hiring manager name (if known), how the user found the role (referral, source, job board), and one specific signal from the JD or the company's public writing worth referencing. Skip questions the user already answered in the prompt. One turn, three questions max.

2. **Frame check.** Run the IC vs people-leadership scan on the JD. Decide: standard EM, hybrid opening, or flag back to the user that an IC template fits better.

3. **Bullet matching.** Read the JD, identify its top three signals, look them up in the table, and select three bullets. Do not default to the same three every time.

4. **Draft.** Write the letter using the chosen template and bullets.

5. **Verification pass (silent, before presenting).** Run two scans on the draft and fix issues without narrating them in the response:
   - **Punctuation scan:** Search for em dashes (the long dash character), en dashes (the medium dash character), and the " - " pattern used as an em dash substitute. Replace each with the appropriate comma, colon, semicolon, or parentheses based on context.
   - **Metrics scan:** For every number or specific claim in the draft, confirm it appears in the master resume, the experience documents, or this skill file without a [verify] flag. If a number is [verify]-tagged or unsourced, either remove the metric or replace it with a qualitative phrasing. Never ship an unverified number.

6. **Present with selection logic.** When delivering the draft, briefly note which JD signals drove which bullet picks so the user can swap quickly. Example: "I picked Fitbit for the turnaround signal, Equifax platform for the developer experience signal, and Nowfloats Technologies Pvt Ltd for the team scaling signal. Swap any if the priority feels off."
