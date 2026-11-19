# Job Agent (India)

An AI agent you chat with in your terminal. It reads your resume, asks what kind of job you want,
searches Indian job boards and company career pages, ranks every job against your profile, and,
when you ask it to, opens a browser and fills in applications for you. It stops and asks before
anything is submitted.

**Searches India only.** Every job it keeps is located in India (including remote roles in India).
Locations outside India are refused.

```
you › My resume is at ~/Documents/Asha_Rao_Resume.pdf
agent › Got it: Python developer, 1 year of experience, strong in Django, SQL, Docker...
        A few questions: which roles, which cities (or remote), full-time or internship, minimum salary?
you › Python developer or backend roles, Bengaluru or remote, full-time, at least 6 LPA
  → searching job boards and company career pages
  → Scoring 25 jobs against your profile ...
agent › | ID | Title                      | Company  | Location  | Score | Source      |
        | 41 | Backend Engineer (Python)  | Groww    | Bengaluru | 91    | company     |
        | 17 | Python Developer           | ...      | Remote    | 86    | naukri      |
you › apply to 41 and 17
```

## How it works

| Step | What happens |
|---|---|
| Resume | Claude reads your PDF/DOCX/TXT resume into a structured profile (skills, experience, education, suggested roles). |
| Preferences | The agent asks about roles, cities or remote, job type, experience level, salary, companies or keywords to avoid, and how recent postings should be. Everything is saved for next time. |
| Search | Runs all sources in parallel, keeps only India jobs, and removes duplicates across sites. |
| Ranking | A fast keyword pre-filter, then Claude scores the top 25 (0-100) with reasons, missing skills and red flags (e.g. "needs 5+ years"). |
| More openings | The agent can also use web search to find openings on company career pages and other Indian sites (foundit, Instahyre, Cutshort, Wellfound, Hirist...) and add them. |
| Apply | When you name jobs to apply to, a visible Chrome window opens. Claude reads the form, fills it from your profile, uploads your resume, writes cover letters and short answers, and asks you for anything it doesn't know. It shows you every answer and waits for your OK before the final Submit. |

### Job sources

| Source | How | Notes |
|---|---|---|
| LinkedIn | public job search (via [python-jobspy](https://github.com/speedyapply/JobSpy)) | Rate-limits heavy use; descriptions are fetched for top matches |
| Indeed India | via python-jobspy | |
| Naukri | via python-jobspy | Descriptions are fetched for top matches |
| Internshala | its listing pages | Internships and fresher jobs |
| Company career pages | official public APIs of Greenhouse, Lever, Ashby, SmartRecruiters | Starts with 25 companies hiring in India (Databricks, MongoDB, Okta, Zscaler, Stripe, Groww, Paytm, Meesho, CRED, Freshworks...). Ask the agent to "track Razorpay" to add more. |
| Glassdoor, Google Jobs | via python-jobspy | Off by default (often blocked): `/set sources linkedin indeed naukri internshala company_boards glassdoor google` |
| Any other site | Claude's web search | The agent saves real postings it finds |

## Setup

You need Python 3.10+ and a Claude API key from https://console.anthropic.com.

```bash
git clone <this repo> job-agent && cd job-agent
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .
playwright install chromium          # the browser the agent drives

export ANTHROPIC_API_KEY=sk-ant-...  # Windows (PowerShell): $env:ANTHROPIC_API_KEY="sk-ant-..."
job-agent
```

If you'd rather it drive your installed Google Chrome, run `/set browser_channel chrome` inside the app.

### Log in to job sites once

Easy Apply on LinkedIn, Naukri's Apply, and Indeed Apply need you to be logged in. Log in once in the
agent's own browser and it remembers you:

```
/login linkedin
/login naukri
/login indeed
/login internshala
```

The agent never types passwords. If it hits a login, OTP or CAPTCHA while applying, it asks you to
handle it in the browser window and then carries on.

## Using it

Just chat. Some things you can say:

- "My resume is at C:\Users\me\Downloads\resume.pdf"
- "Find internships in data science, Pune or work from home, stipend at least 10k"
- "Only show jobs posted in the last 3 days" · "Avoid service companies like X and Y"
- "Show me details for 23" · "Why is 31 a stretch?"
- "Look for openings on Zepto's and Razorpay's career pages"
- "Apply to 23, 31 and 40"
- "My notice period is 30 days and expected CTC is 8 LPA" (saved and reused in forms)

Commands (no AI call, instant):

| Command | |
|---|---|
| `/jobs [n]` | Top-ranked jobs |
| `/job <id>` · `/open <id>` | Show a job · open it in the browser |
| `/apps` | Application history and outcomes |
| `/profile` · `/prefs` · `/answers` · `/companies` | What the agent knows |
| `/login <site>` | Log in to linkedin, naukri, indeed, internshala, glassdoor, foundit, wellfound, or any URL |
| `/settings` · `/set <key> <value>` | View or change settings |
| `/reset` · `/quit` | New conversation (data kept) · exit |

### Safety rails (enforced in code)

- **You approve every submission.** The final Submit click is blocked until you approve it in the
  terminal. You can approve, cancel, or type a correction. Turn this off only if you want it fully
  hands-off: `/set auto_submit true`.
- **No made-up answers.** Anything not in your resume or saved answers (salary, notice period,
  relocation, custom questions) is asked of you, and you can have it remembered.
- **No passwords, no payments, no new accounts.** Logins, OTPs and CAPTCHAs are handed to you.
- **Applies only when you ask**, and confirms the list of jobs before starting.
- Press **Ctrl+C** during an application to stop. The tab stays open so you can finish by hand.

### Settings

| Key | Default | Meaning |
|---|---|---|
| `model` | `claude-opus-5-5` | Claude model for everything |
| `chat_effort` / `apply_effort` / `scoring_effort` | `medium` / `medium` / `low` | How hard Claude thinks for each part |
| `auto_submit` | `false` | Submit without asking you first |
| `sources` | linkedin indeed naukri internshala company_boards | Where to search |
| `results_per_query` | 15 | Results per role and city per board |
| `max_queries_per_source` | 4 | Role × city combinations per board |
| `llm_score_top_n` | 25 | How many jobs Claude scores per search |
| `max_apply_steps` | 60 | Step limit for one application |
| `headless` | `false` | Hide the browser (not recommended: you can't solve CAPTCHAs) |
| `browser_channel` | none | `chrome` to use your installed Chrome |

## Your data

Everything stays on your computer in `~/.job-agent` (change it with `--home` or `JOB_AGENT_HOME`):
the job database, a copy of your resume, saved form answers, generated cover letters, the browser
profile with your site logins, and a log of every application step (`logs/`). Your resume, job
descriptions and page contents are sent to the Claude API to do the work.

## Things to know

- **Job-site rules.** LinkedIn, Naukri and Indeed don't allow automated use in their terms of service.
  Heavy automated searching or applying can get an account rate-limited or restricted. Keep volumes
  reasonable, and use it at your own risk. The company career-page APIs are public and meant for this.
- **Cost.** Every chat turn, job scoring and application step is a Claude API call. A search that scores 25
  jobs and a typical application are each a modest number of calls. Lower `llm_score_top_n`, or
  `/set model claude-sonnet-5-5`, to spend less.
- **Hard cases.** Some company portals (e.g. Workday) make you create an account per company, and some
  forms use unusual widgets. The agent asks you to do those parts, or marks the job "needs_manual" so
  you can finish it yourself.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The tests cover the India location rules, the source parsers, storage and ranking, the chat agent's tool
loop (with a scripted stand-in for Claude), and a full application run in headless Chromium against a
local form: filling fields, uploading a resume, asking a question, refusing an unapproved Submit,
then submitting after approval. Set `JOB_AGENT_TEST_CHROMIUM` to a Chromium binary if the browser test
can't find one.

```
job_agent/
  cli.py            terminal app and /commands
  agent.py          the chat agent and its tools
  applier.py        the apply agent (browser tools, submit gate)
  browser.py        Playwright wrapper: page snapshots and actions
  search.py         runs the sources, India filter, dedup, ranking
  matching.py       keyword pre-filter and Claude scoring
  sources/          LinkedIn/Indeed/Naukri/Glassdoor/Google (jobspy), Internshala, company boards
  enrich.py         fetches full descriptions for top matches
  resume.py         resume parsing, cover-letter .docx
  india.py          India-only location rules
  llm.py            Claude API wrapper (streaming, structured output, fallbacks)
  db.py             SQLite storage
```
