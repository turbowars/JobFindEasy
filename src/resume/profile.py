"""LOCKED profile data for Dheeraj Sampath.

Single source of truth: name, contact, education, certifications, work history
(company / title / location / dates), per-role bullet pool, and the master
skills tree. Everything in this file is immutable from the LLM's perspective —
the resume generator may pick FROM these structures, never invent or rewrite
the locked fields. Bullets MAY be lightly rephrased to match JD language as
long as numbers, percentages, and named tools/products are preserved.
"""

from __future__ import annotations

NAME = "Dheeraj Sampath"

# Single contact line, comma-piped, rendered centered under the name. Mirrors
# what the legacy resume.py CONTACT_LINE shipped — EM phone/email + portfolio
# domain that's been used on every recent application.
CONTACT_LINE = (
    "Austin, TX | 248-873-8929 | dheerajsampath@proton.me | "
    "linkedin.com/in/evolvingdx | dheerajsampath.com"
)

# Locked facts the LLM has historically drifted on. Restated in the prompt
# preamble so the model doesn't infer "12+ years" from the resume's earliest
# date (2012) and undercount.
YEARS_OF_EXPERIENCE = "15+"


# Application-form defaults. NOT rendered on the resume — these answer common
# fields ("desired salary", "notice period", "work authorization") that show
# up on every job application. The dashboard's "Apply with Claude" prompt
# pulls from here so these answers stay consistent across applications and
# are owned alongside the rest of the profile.
APPLICATION_DEFAULTS = {
    "email": "turbowars@gmail.com",  # applications inbox (resume header uses dheerajsampath@proton.me)
    "comp_expectation": "$220k+",
    "notice_period": "No notice period - immediately available to join",
    "open_to": "Remote (US), Hybrid (Bay Area / NYC). Not relocating.",
    "targeting": "Engineering Manager / Staff Frontend / Frontend Platform leadership roles",
    "work_authorization": "Authorized to work in the US, requires H-1B transfer. Sponsorship REQUIRED.",
}

# Education (locked, exact strings).
EDUCATION = [
    "Master of Business Administration, Digital Entrepreneurship - Strayer University, Herndon, VA",
    "Bachelor of Technology, Computer Science - Mahatma Gandhi Institute of Technology, India",
]

# Certifications that always appear.
CERTIFICATIONS_ALWAYS = [
    "AI-Driven Leadership Program, Stanford University",
    "Certified Scrum Master (CSM), Scrum Alliance",
]

# JD-conditional certification — pipeline picks ONE based on JD signals.
CERTIFICATIONS_CONDITIONAL = {
    "cua": "Certified Usability Analyst (CUA), Human Factors International",
    "microsoft": "Microsoft Specialist, Programming in HTML5 with JavaScript and CSS3 (Exam 70-480)",
}


# Per-role experience. Company, title, location, dates are LOCKED. Bullet pool
# is the source of truth for facts; pipeline picks 4-7 per role and may
# rephrase verbs/emphasis to mirror JD language.
# Equifax title is the only role whose title is JD-flexed; pipeline computes
# `[JD title] (Tech Lead)` for IC tracks and `[JD title] (Engineering Lead)`
# for EM tracks. The `title` field below is the IC-track default and is
# overridden at render time.
EXPERIENCE = [
    {
        "key": "equifax",
        "company": "Equifax",
        "title": "Engineering Lead, Frontend Platform",
        "location": "Austin, TX",
        "dates": "Aug 2022 - Present",
        "descriptor": (
            "Own the React and TypeScript micro-frontend platform powering "
            "Equifax's regulated consumer identity and credit product suite. "
            "Set technical direction across three product teams while staying "
            "hands-on in load-bearing platform code."
        ),
        "bullet_pool": [
            "Led front-end architecture and program strategy, partnering with three cross-functional teams.",
            "Directed governance for 10+ major launches with 100% on-time delivery and on-budget execution.",
            "Redesigned front-end architecture and developer tooling, **improving scalability and user satisfaction 50%**.",
            "Increased project delivery efficiency 35% through structured cross-functional coordination.",
            "Standardized React and micro-frontend platform, **reducing build cycles 25%** and eliminating cross-team release blockers.",
            "Built and grew the **shared Storybook component library** powering the React + Module Federation platform. Three product teams reuse the same contract-tested components, design-token theming, and accessibility checks, eliminating duplicated UI work across teams.",
            "Bridged product, design, and engineering alignment, cutting rework 20%.",
            "Shipped a Module Federation-based micro-frontend platform letting three product teams release independently against a shared design-token system and contract-tested component library.",
            "Designed and built an internal developer platform (CLI scaffolding, ephemeral preview environments, shared CI templates) in Go and Node.js, deployed on AWS ECS Fargate with Terraform-managed infrastructure. **Reduced new-service onboarding from days to under an hour.**",
            "Built an LLM-powered code-review assistant using the Anthropic Claude API combined with AST-level analysis. The agent flags accessibility regressions, design-system violations, unsafe PII handling, and performance anti-patterns on every pull request. **Adopted by 40+ engineers and cut post-merge defects ~30%.**",
            "Led migration of a legacy Angular monolith to Next.js 14 with React Server Components, Server Actions, and edge rendering on Cloudflare Workers. **Improved LCP 52% and cut initial JavaScript payload 38%.**",
            "Architected the BFF layer using GraphQL (Apollo Server) on Node.js and AWS Lambda, backed by PostgreSQL (RDS) and DynamoDB. Collapsed 3-4 REST round-trips per dashboard view into a single typed query, **dropping Time-to-Interactive from 4s to 1.6s**.",
            "Instrumented observability for the frontend fleet using OpenTelemetry, Core Web Vitals, and custom RUM dashboards in Datadog. Wired Sentry for exception tracking and session replay; caught three high-severity regressions before production.",
            "Authored an event-driven data pipeline for UI telemetry and analytics using AWS EventBridge, SQS, and SNS to fan events into S3 data-lake tables queried via Athena, plus a GCP Pub/Sub and BigQuery mirror for the analytics team.",
            "Partnered with platform security and compliance on SOC 2, GLBA, and CCPA controls covering UI telemetry, PII handling, session fingerprinting, and third-party script governance.",
            "Designed feature-flag and progressive-delivery architecture on LaunchDarkly with typed flag definitions generated from a central schema. Enabled trunk-based development and safe canary rollouts across three product teams.",
            "Authored the RFCs and ran the architecture reviews that shaped platform direction across three teams. Mentored senior and staff-track ICs through pairing, design reviews, and written architectural coaching.",
        ],
    },
    {
        "key": "midigator",
        "company": "Midigator (acquired by Equifax)",
        "title": "Frontend Architect",
        "location": "Austin, TX",
        "dates": "Feb 2022 - Aug 2022",
        "descriptor": (
            "Led frontend architecture at Midigator, a payment-dispute and "
            "chargeback-management SaaS serving enterprise merchants. "
            "Hands-on owner of the embeddable partner SDK, the core React "
            "dashboard, and the design-system foundation."
        ),
        "bullet_pool": [
            "Architected an embeddable React SDK for partner platforms with full Shadow DOM isolation, runtime theming via CSS custom properties, a TypeScript-typed event bus, and a tree-shaken Rollup bundle. **Shipped into four partner integrations within the first quarter.**",
            "Designed the micro-frontend composition layer and shared design system that carried forward as the reference implementation for the combined Equifax + Midigator frontend platform post-acquisition.",
            "Redesigned the BFF contract with the backend team (Node.js + PostgreSQL) to collapse three REST round-trips into a single GraphQL query. **Cut dashboard Time-to-Interactive from 4.2s to 1.6s.**",
            "Introduced Playwright end-to-end tests and Vitest unit-coverage gates in CI (GitHub Actions). **Regression escapes dropped ~60%** and the team moved to trunk-based deploys.",
            "Built the theming and tokenization layer for embeddable components so partners could brand the experience without forking; shipped as a runtime theming API backed by design-token JSON and Style Dictionary output.",
            "Stood up the **Storybook-driven shared component library** that survived the Equifax acquisition and became the reference UI implementation for the combined platform; standardized component documentation, visual regression in Chromatic, and accessibility audits.",
            "**Cut time-to-market 35%** through rapid prototyping and workflow optimization across the embeddable SDK and the core React dashboard.",
            "Cultivated a collaborative engineering culture through structured mentorship and architectural pairing across the small frontend team.",
        ],
    },
    {
        "key": "ta_digital_usa",
        "company": "TA Digital, USA (Adobe Platinum Partner)",
        "title": "UI Architect",
        "location": "Minneapolis, MN",
        "dates": "Sep 2019 - Feb 2022",
        "descriptor": (
            "Onshore architect across two enterprise engagements (AARP and "
            "Strategic Education Inc.) leading frontend architecture, "
            "performance engineering, accessibility, and multi-brand "
            "component-system work while staying ~70% hands-on in code."
        ),
        "bullet_pool": [
            "Architected ~25 reusable, themeable AEM and React components spanning marketing, program discovery, enrollment, and post-enrollment for Strategic Education. Shared across Strayer University and Sophia Learning as a multi-brand design system with Style Dictionary tokens.",
            "Led the end-to-end frontend redesign of strayer.com with a component-first architecture that compressed page-build times for marketing and enabled rapid campaign iteration.",
            "Built the tuition calculator (the most complex tool in the engagement) as a multi-step TypeScript wizard integrated with a Node.js pricing-rules API on PostgreSQL. Surfaces program pricing across financial-aid rules, employer discounts, transfer-credit adjustments, and military benefits.",
            "Drove a full performance program using Lighthouse as the benchmark: lazy loading, code splitting, critical CSS extraction, font optimization, AEM dispatcher caching, and CDN tuning on CloudFront. **Combined program lifted Lighthouse scores from the 40s into the 90s** and brought Core Web Vitals into the Good band.",
            "**Improved page load speeds 400%** on the largest client engagement by standardizing SPA frameworks and shipping a reusable performance-engineering playbook.",
            "Owned WCAG 2.1 AA accessibility compliance (AAA met where feasible). Wired automated a11y tooling into CI and paired it with manual screen-reader testing across NVDA, JAWS, and VoiceOver.",
            "Architected and implemented a new AEM-based retirement calculator for AARP as a reusable module: 5-7 input wizard, conditional branching on employment and benefit eligibility, save-and-return for partial sessions.",
            "Operated as onshore technical bridge for a distributed team across time zones for two years without a missed milestone.",
            "Introduced TypeScript, Storybook, design-system patterns, and Lighthouse-based performance regression gates across client teams; standardized CI/CD on Jenkins and GitHub Actions.",
            "Drove adoption of scalable AI-assisted personalization (product ranking and content recommendations) integrating vendor APIs with custom React rendering. **Boosted delivery quality and feature velocity 30%.**",
            "Reduced turnaround times 25% through agile process improvements and led system acceptance testing ensuring 100% technical and business alignment.",
        ],
    },
    {
        "key": "ta_digital_india",
        "company": "TA Digital, India (Adobe Platinum Partner)",
        "title": "Principal Engineer",
        "location": "Hyderabad, India",
        "dates": "Feb 2018 - Sep 2019",
        "descriptor": (
            "Frontend architect and Scrum Master / Tech Lead across two "
            "global enterprise engagements (Bose and Fitbit) before being "
            "selected for the onshore UI Architect role in the US."
        ),
        "bullet_pool": [
            "Stepped into a year-long stalled program and **shipped the Fitbit global website redesign on time with zero P0/P1 defects** as Scrum Master and Tech Lead of a 14-person cross-functional team (5 AEM developers, 7 UI developers, 2 QA engineers).",
            "Designed and led delivery of 7-12 reusable AEM and responsive components consumed across all fitbit.com pages and five locales via AEM multi-site management.",
            "Integrated Adobe Target A/B testing into the component architecture so Fitbit's marketing and growth teams could run experiments on hero placements and pricing without engineering involvement per test.",
            "Raised code quality across the 14-person team through shared patterns, code-review standards, and onshore-offshore pairing. **Reduced sprint carryover 35-40% and cut late-stage rework ~50%.**",
            "Architected the frontend authentication layer for Bose (login, signup, password reset, account recovery, session management) as reusable, brand-aligned responsive components. **Integrated end-to-end with Okta** including token lifecycles, redirect flows, session expiration, and silent re-authentication.",
            "Established testing and error-handling patterns on Bose that carried forward as a standard across Fitbit, AARP, and Strategic Education engagements.",
        ],
    },
    {
        "key": "nowfloats",
        "company": "nowfloats",
        "title": "Principal Engineer (UI / UX)",
        "location": "Hyderabad, India",
        "dates": "Mar 2017 - Feb 2018",
        "descriptor": (
            "Owned frontend architecture for the nowfloats web platform, a "
            "consumer and merchant product serving 10M+ SMB merchants across "
            "India and Southeast Asia. Led a 12-engineer UI team."
        ),
        "bullet_pool": [
            "Architected the mobile-first Progressive Web App that **10M+ SMB merchants** used to manage their digital storefronts on low-end Android devices over unreliable networks. Implemented offline-first sync, service workers, and background sync queues.",
            "Built the real-time merchant dashboard using WebSockets and Redis pub/sub for live order, inquiry, and chat notifications. Designed the reconnection, backoff, and message-replay protocol for mobile networks that drop connections frequently.",
            "Designed schema and data-access patterns for merchant-facing services across MongoDB (catalog, profiles, unstructured content) and MySQL (transactional commerce data) as a hybrid NoSQL+SQL model.",
            "Set the hiring bar, technical interview loop, and onboarding playbook. **Grew the team from 5 to 12 engineers without regressing delivery velocity.**",
            "Introduced React alongside the existing AngularJS codebase as a path-forward framework. Migrated key merchant flows incrementally and documented the migration pattern.",
        ],
    },
    {
        "key": "deloitte",
        "company": "Deloitte Digital Studio",
        "title": "Senior Engineer, Frontend / UI",
        "location": "Mumbai, India",
        "dates": "Aug 2014 - Mar 2017",
        "descriptor": (
            "Shipped responsive web and hybrid-mobile experiences for "
            "Fortune 500 retail, energy, telecom, and financial-services "
            "clients out of the Deloitte Digital Mumbai studio."
        ),
        "bullet_pool": [
            "Drove adoption of AngularJS as the SPA framework of choice across multiple engagements; authored patterns for directives, services, and routing reused across projects.",
            "Built a custom JavaScript framework with mobile and Angular adaptability that became the studio's internal foundation for hybrid and responsive apps.",
            "Delivered ExxonMobil B2B e-commerce: AngularJS commerce platform serving enterprise procurement and ordering, with a Bootstrap-based responsive implementation across mobile navigation, touch-friendly form controls, and adaptive data tables for field-operator devices.",
            "Delivered Telstra (Australia's largest telecom carrier) AEM frontend across consumer and business web estate: marketing hero banners, product-plan selectors, support-flow forms, account-management tools.",
            "Mentored junior and mid-level engineers through pairing, code review, and weekly architecture sessions. Owned hiring panels and technical interview design for the Mumbai studio's UI practice.",
        ],
    },
    {
        "key": "neudesic",
        "company": "Neudesic (Microsoft Gold Partner)",
        "title": "Consultant, UX and Frontend Engineering",
        "location": "Hyderabad, India",
        "dates": "May 2012 - Aug 2014",
        "descriptor": (
            "Delivered enterprise web applications and branded CMS solutions "
            "across retail, financial services, government, healthcare, and "
            "consumer-electronics clients on the Microsoft stack."
        ),
        "bullet_pool": [
            "Programmed a custom sticky-browser jQuery scrollbar plugin reused across multiple engagements that earned direct client recognition.",
            "Introduced LESS CSS and Twitter Bootstrap as the UX India region's standard frontend stack, baseline for future client engagements.",
            "Developed a custom Tile Image Gallery jQuery plugin for SharePoint 2013 adopted across multiple government and enterprise SharePoint engagements.",
            "Built complex interactive data visualizations using Highcharts.js and D3.js, including a choropleth heat map for PricewaterhouseCoopers' Integrated Global Compliance Services.",
            "Authored the HTML5 standalone offline app of the Neudesic Pulse product, one of the earliest PWA-style offline experiences at Neudesic.",
        ],
    },
]


# Master project pool. The LLM picks 3-5 most relevant per JD (or omits the
# whole section if none fit). Each entry is locked: the LLM may reorder and
# filter but cannot invent new projects or alter facts.
PROJECTS_MASTER = [
    {
        "name": "AI Code Review Agent",
        "description": (
            "Next.js 14 + Anthropic Claude API + AST parsing. Autonomous PR "
            "reviewer running accessibility, security, design-system, and "
            "performance checks. In production at Equifax, adopted by 40+ "
            "engineers; cut post-merge defects ~30%."
        ),
        "tags": ["ai", "llm", "claude", "code-review", "platform", "devex"],
    },
    {
        "name": "MCP-Powered Developer Assistant",
        "description": (
            "Built a Model Context Protocol (MCP) server exposing internal "
            "documentation, design tokens, and component APIs to Claude Code "
            "so engineers can ask architectural questions in-editor."
        ),
        "tags": ["ai", "mcp", "devex", "platform", "claude-code"],
    },
    {
        "name": "RAG over Engineering Docs",
        "description": (
            "pgvector + OpenAI embeddings + FastAPI backend with a streaming "
            "Next.js UI on the Vercel AI SDK. Semantic search across 2,000+ "
            "internal architecture decision records and runbooks."
        ),
        "tags": ["ai", "rag", "vector-db", "search", "next.js"],
    },
    {
        "name": "Edge-Rendered Identity Flows",
        "description": (
            "Cloudflare Workers + Hono delivering sub-50ms TTFB globally for "
            "consumer-facing authentication screens."
        ),
        "tags": ["edge", "auth", "identity", "performance", "cloudflare"],
    },
    {
        "name": "Multi-Brand Design-Token Pipeline",
        "description": (
            "Style Dictionary + Storybook + Chromatic. The design-token "
            "architecture that backed the Strategic Education multi-brand "
            "system and informed later Midigator and Equifax work."
        ),
        "tags": ["design-system", "storybook", "tokens", "multi-brand"],
    },
]


# Master skills tree. Pipeline picks ~10-14 categories per JD and reorders so
# JD-relevant categories surface first. Items within a category may be filtered
# but never invented.
SKILLS_MASTER = [
    {
        "label": "Engineering Leadership",
        "items": [
            "People management",
            "Team leadership",
            "Hiring",
            "Mentorship",
            "Technical strategy",
            "Cross-functional alignment",
            "Roadmap ownership",
            "Stakeholder communication",
            "Communication skills",
            "Architecture reviews",
            "Hiring loops",
            "Onboarding playbooks",
            "Performance reviews",
            "Career laddering",
        ],
    },
    {
        "label": "Languages",
        "items": [
            "TypeScript",
            "JavaScript (ES2023)",
            "Go",
            "Python",
            "HTML5",
            "CSS3",
            "SQL",
            "GraphQL",
            "Bash",
            "C#",
            "Java",
        ],
    },
    {
        "label": "Frontend Frameworks and Libraries",
        "items": [
            "Frontend development",
            "React 18",
            "Next.js 14 (App Router, Server Actions, RSC, ISR, SSR, SSG)",
            "React Server Components",
            "Angular (2-17)",
            "AngularJS",
            "Redux",
            "Redux Toolkit",
            "TanStack Query",
            "Zustand",
            "RxJS",
            "NgRx",
            "Vue.js",
            "Svelte",
            "Web Components",
            "Lit",
            "Stencil",
        ],
    },
    {
        "label": "Build Tools, Bundlers, and Monorepos",
        "items": [
            "Vite",
            "Webpack 5",
            "Turbopack",
            "Rollup",
            "esbuild",
            "Module Federation",
            "Nx",
            "Turborepo",
            "Lerna",
            "pnpm workspaces",
            "Yarn workspaces",
            "Babel",
            "SWC",
            "TypeScript Project References",
        ],
    },
    {
        "label": "UI, Styling, and Design Systems",
        "items": [
            "Tailwind CSS",
            "CSS-in-JS (styled-components, Emotion, vanilla-extract)",
            "CSS Modules",
            "SASS/SCSS",
            "OOCSS",
            "PostCSS",
            "Material UI",
            "shadcn/ui",
            "Radix UI",
            "Headless UI",
            "Storybook",
            "Chromatic",
            "Figma tokens",
            "Style Dictionary",
            "Reusable component libraries",
            "Multi-brand theming",
            "User experience design",
            "UX design",
            "UI design",
            "WCAG 2.1 AA / 2.2 AA",
            "Core Web Vitals",
            "Lighthouse",
            "Progressive Web Apps (PWA)",
            "Internationalization (i18n)",
            "Localization (l10n)",
            "A/B testing platforms (Adobe Target, LaunchDarkly experiments)",
            "Personalization engines",
        ],
    },
    {
        "label": "Micro-Frontend and Platform Architecture",
        "items": [
            "Module Federation",
            "single-spa",
            "Web Components",
            "Shadow DOM isolation",
            "runtime theming",
            "contract-tested component libraries",
            "design-token pipelines",
            "BFF patterns",
            "edge rendering",
            "SSR/SSG/ISR",
            "streaming UIs",
            "multi-tenant UI platforms",
            "event-driven UIs",
        ],
    },
    {
        "label": "Backend, APIs, and Integration",
        "items": [
            "Node.js",
            "Express",
            "Next.js API routes",
            "NestJS",
            "Fastify",
            "tRPC",
            "Go (Gin, Fiber)",
            "Python (FastAPI, Flask)",
            "Java (Spring, Spring Boot)",
            "REST",
            "GraphQL (Apollo Server and Client)",
            "gRPC",
            "WebSockets",
            "Server-Sent Events",
            "OpenAPI / Swagger",
            "OAuth 2.0",
            "OIDC",
            "SAML",
            "JWT",
            "Okta",
            "Auth0",
            "federated SSO",
        ],
    },
    {
        "label": "Databases and Data Stores",
        "items": [
            "PostgreSQL",
            "MySQL",
            "SQL Server",
            "MongoDB",
            "DynamoDB",
            "Firestore",
            "Redis",
            "pgvector",
            "Pinecone",
            "Weaviate",
            "Elasticsearch",
            "BigQuery",
        ],
    },
    {
        "label": "AWS Cloud",
        "items": [
            "Lambda",
            "EC2",
            "ECS",
            "Fargate",
            "S3",
            "CloudFront",
            "API Gateway",
            "DynamoDB",
            "RDS",
            "SQS",
            "SNS",
            "EventBridge",
            "Step Functions",
            "Kinesis",
            "IAM",
            "Cognito",
            "Route 53",
            "Secrets Manager",
            "CloudWatch",
            "X-Ray",
        ],
    },
    {
        "label": "GCP Cloud",
        "items": [
            "Cloud Run",
            "Cloud Functions",
            "GKE (Kubernetes)",
            "Firestore",
            "Cloud Storage",
            "Pub/Sub",
            "BigQuery",
            "Cloud SQL",
            "Identity Platform",
            "Cloud Build",
            "Artifact Registry",
        ],
    },
    {
        "label": "Edge and Serverless",
        "items": [
            "Vercel",
            "Cloudflare Workers",
            "Cloudflare Pages",
            "Cloudflare R2",
            "Cloudflare KV",
            "Netlify",
            "Deno Deploy",
            "AWS Lambda@Edge",
        ],
    },
    {
        "label": "AI / LLM Engineering",
        "items": [
            "Anthropic Claude API (Opus, Sonnet, Haiku)",
            "OpenAI API (GPT-4, GPT-4o, o1)",
            "Vercel AI SDK",
            "LangChain",
            "LangGraph",
            "Model Context Protocol (MCP)",
            "Retrieval-Augmented Generation (RAG)",
            "semantic search",
            "vector databases",
            "embeddings",
            "prompt engineering",
            "evals",
            "agentic workflows",
            "tool use",
            "function calling",
            "AI code-review agents",
        ],
    },
    {
        "label": "AI Coding Tools",
        "items": [
            "Generative AI tools",
            "Claude Code",
            "Cursor",
            "GitHub Copilot",
            "Windsurf",
            "v0",
            "AI-assisted code review",
            "AI-augmented delivery",
        ],
    },
    {
        "label": "Data Engineering and Orchestration",
        "items": [
            "Apache Airflow",
            "DAG authoring and scheduling",
            "data pipeline orchestration",
            "ETL / ELT workflows",
            "batch and streaming pipelines",
            "BigQuery",
            "Pub/Sub",
            "Kinesis",
        ],
    },
    {
        "label": "Infrastructure and DevOps",
        "items": [
            "Docker",
            "Kubernetes",
            "Helm",
            "Terraform",
            "Pulumi",
            "Ansible",
            "Jenkins",
            "GitHub Actions",
            "CircleCI",
            "GitLab CI",
            "ArgoCD",
            "Vercel",
            "trunk-based development",
            "feature flags (LaunchDarkly, Split)",
            "blue-green deploys",
            "canary releases",
            "progressive delivery",
        ],
    },
    {
        "label": "Observability, Performance, and Quality",
        "items": [
            "Datadog",
            "Sentry",
            "OpenTelemetry",
            "Grafana",
            "Prometheus",
            "New Relic",
            "Adobe Analytics",
            "Adobe Launch",
            "Google Analytics",
            "real-user monitoring (RUM)",
            "Core Web Vitals",
            "Lighthouse CI",
            "WebPageTest",
            "axe accessibility",
            "SEO (structured data, semantic HTML)",
            "Web performance optimization",
            "Incident resolution",
            "Incident response",
            "Postmortems",
            "Blameless retrospectives",
        ],
    },
    {
        "label": "Testing",
        "items": [
            "Vitest",
            "Jest",
            "Jasmine",
            "Karma",
            "Mocha",
            "Testing Library",
            "Playwright",
            "Cypress",
            "Puppeteer",
            "Storybook interaction tests",
            "contract testing (Pact)",
            "visual regression (Chromatic, Percy)",
            "unit, integration, E2E, and a11y testing",
        ],
    },
    {
        "label": "Architecture Patterns",
        "items": [
            "Micro-frontends",
            "Module Federation",
            "event-driven systems",
            "CQRS",
            "distributed systems",
            "multi-tenancy",
            "edge computing",
            "BFF",
            "hexagonal architecture",
            "DDD",
            "clean architecture",
            "functional programming",
            "reactive programming",
        ],
    },
    {
        "label": "Process and Delivery",
        "items": [
            "Agile",
            "Scrum",
            "Kanban",
            "SAFe",
            "SDLC",
            "Jira",
            "Confluence",
            "Linear",
            "Notion",
            "RFCs",
            "design reviews",
            "code reviews",
            "cross-geo team coordination",
            "KPI dashboards",
        ],
    },
    # Web3 / blockchain stack — built on a personal-project NFT commerce store
    # (mint + secondary market). Pipeline only surfaces this category when the
    # JD names blockchain / crypto / Web3 vocabulary.
    {
        "label": "Blockchain and Web3 (personal projects)",
        "items": [
            "Solidity",
            "Smart contracts",
            "Web3",
            "Ethereum",
            "Polygon",
            "EVM-compatible chains",
            "ERC-721",
            "ERC-1155",
            "NFT minting",
            "NFT marketplace",
            "Wallet integration (MetaMask)",
            "wagmi",
            "viem",
            "ethers.js",
            "Hardhat",
            "Foundry",
            "IPFS",
        ],
    },
    # Domain + methodology vocabulary the LLM may surface when the JD names
    # them and Dheeraj's experience genuinely supports the claim. Each item
    # below is backed by a specific past role; the LLM is told to pick from
    # this list (never invent new terms) but it's free to add JD-named terms
    # to other categories if the experience supports them.
    {
        "label": "Domains and Methodologies",
        "items": [
            # SaaS shape — Midigator (chargeback SaaS), Equifax consumer suite, Strayer
            "SaaS",
            "B2B SaaS",
            "Multi-tenant SaaS",
            # Consumer / B2B mix — Fitbit, Bose, AARP, Telstra, ExxonMobil, nowfloats
            "Consumer Web",
            "B2B",
            "B2C",
            "Enterprise Web Applications",
            # Industry verticals he's actually shipped for
            "Fintech",
            "Consumer Credit and Identity",
            "Payments and Chargeback",
            "EdTech",
            "HealthTech",
            "Telecommunications",
            "Energy / Utilities",
            "Public Sector",
            "E-commerce",
            # Engineering methodologies (DevOps lives here as a practice term;
            # the tooling sits under Infrastructure and DevOps above)
            "DevOps",
            "DevEx / Developer Experience",
            "Platform Engineering",
            "Site Reliability Engineering (SRE)",
            "Continuous Delivery",
            "Trunk-based Development",
            "Progressive Delivery",
            "Microservices Architecture",
            "Event-Driven Architecture",
            "API-first Design",
            "Headless / Composable Architecture",
            # Compliance / regulatory experience
            "SOC 2",
            "GLBA",
            "CCPA",
            "GDPR",
            "WCAG 2.1 AA Accessibility",
        ],
    },
]
