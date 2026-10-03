# Job applications — how the pipeline works

El Fager finds jobs in Egypt, scores each against Mo's CV, drafts a tailored
application for the good fits, and sends only what Mo approves each morning.
Code: `core/career/`, `core/agents/job_search_agent.py`, `tools/career_tool.py`.

```
 job hunt (armed for 02:00)                   morning                     day
 check replies → gather → score → draft  ──►  review page /jobs  ──►  send (paced)
 (Gmail)         (Wuzzuf,  (Claude,  (Claude)  Mo ticks, sends        email · Wuzzuf ·
                  LinkedIn, min 60)                                   LinkedIn · site form
                  company
                  sites)
```

## Getting started (say these to El Fager)

1. **"Import my CV from C:\Users\Mohab1\...\CV.pdf"** — reads it into the profile
   the scores and letters come from, and the file that gets attached. Mo has two:
   the main CV (AI, data and business-analysis roles) and **"import my ERP CV
   from ..."** for ERP roles (SAP, Odoo, Dynamics...): a job whose title names ERP
   work is scored, written and sent with the ERP one.
2. **Answer what forms ask:** "my military status is exempted", "my expected
   salary is 15,000", "I can start immediately", "my GPA is 3.2"… `application
   status` lists what's still missing.
3. **Press RUN on "Job hunt"** in the Command Center's AUTOMATIONS panel (ON
   REQUEST): it arms one hunt for 02:00 tonight (press again to cancel), run by
   the scheduler, and the batch is ready by morning. Tonight only -- it never
   repeats. "Prepare my applications" starts one right away instead.
4. **Review** at `http://127.0.0.1:8765/jobs` (dashboard token from
   `data/settings.json`). Nothing is ticked: tick the ones to send and press
   Send. The rest stay saved there, letters and all
   (`data/career/applications.json`), to send another day.
5. **"Switch job applications to live"** once the CV is final. Until then it is
   practice mode: approving marks "would have sent", nothing leaves the machine.

## Mo's main goal: an interview at one of the biggest companies in Cairo

- **Every brain turn** carries a one-line job-hunt status (`core/career/focus.py`):
  interviews, the batch waiting for review, programme deadlines, referral notes.
  The persona says the job hunt comes first; the daily briefing opens with it.
- **Morning nudge** (proactive engine, 7–11 AM): what's waiting on him; a
  programme closing within a week also goes to his phone.
- **Graduate programmes** (`core/career/programmes.json`): the Big 4's and top
  companies' programme pages, read at most weekly by the job hunt, for status,
  deadline and whether fresh graduates can apply. "Which graduate programmes
  are open?"
- **Referrals** (`core/career/referrals.py`): each night, up to
  `referrals_per_day` people at target companies (alumni of his university
  first; set it with "my university is ...") with a drafted connection note and
  referral request. They appear on the review page with Copy buttons; Mo sends
  them himself and marks them sent. **"Import my LinkedIn connections"**
  (LinkedIn: Settings > Data privacy > Get a copy of your data > Connections;
  the newest Connections.csv or LinkedIn zip in Downloads is found) keeps the people he knows at target companies; they're
  asked first, with the message only (no connection note needed).
- **Follow-ups** (`tracker.follow_ups_due`): a sent application with no answer is
  due a follow-up a week later, and once more a week after that, then let go
  (career-ops' cadence). It's in the job-hunt line, the morning nudge and
  `application status`;
  "I followed up with Valeo" records it.

## Borrowed from career-ops

- **"Is this job worth it: <link>"** or **"check the job I copied"** (`evaluate_job`;
  with no link said, the copied one is used): a posting Mo found himself,
  judged on the spot -- closed or open, score, what it asks that his CV lacks. A
  fit waits for the next job hunt, which writes its letter.
- **"What should I learn?"** (`skill_gaps`, `core/career/gaps.py`): the scored
  jobs' "missing" lists grouped into skills, most asked first.
- **Practice interview** after a prep sheet: one question at a time, then what
  landed, what to sharpen and a stronger opening from his CV.
- **CV check on import:** names a missing Experience / Education / Skills
  heading or email that hiring systems look for.
- **Replies:** a rejection is read before an interview mention, and an automatic
  "we received your application" changes nothing (it used to read as an
  interview).
- **Connection notes** are kept under 200 characters, LinkedIn's free-account cap.

- **Scoring rules** (`core/career/rubric.md`): career-ops' ~27k-token evaluation,
  condensed by Claude to ~1.1k tokens and sent with every score (about $1.30 a
  month more). Weigh only requirements the posting itself marks required; a
  nice-to-have gap doesn't sink a score; name hard blockers. Tried on 10 scored
  jobs: same order, tighter gaps, and it passed an entry Power BI role the plain
  prompt had put at 58. Rewrite it by hand if a rule reads wrong.

Kept El Fager's own: the scoring output (a short JSON, not career-ops' A-H
report), cover letters (0 clichés from career-ops' list
in the saved ones), tracking, referral drafting, the job sources.

## Where jobs come from

- Every search term (`search_terms` in `store.DEFAULTS`) on Wuzzuf and LinkedIn
  (`SOURCES` in `job_search_agent.py`); Wuzzuf is read in headless Chromium
  past Cloudflare. Bayt and Forasna are skipped, in the job hunt and in
  "find me jobs" alike. Wuzzuf applications go through Mo's signed-in account.
- The Big 4 by name on Wuzzuf and LinkedIn, plus their own sites: Deloitte's
  Middle East careers site, PwC's Workday, EY's student and careers sites.
- Mo's other picks, treated like the Big 4 (`"opus": true` in `companies.json`):
  IBM, Accenture, Schneider Electric, P&G, Siemens, Microsoft and Nestlé —
  searched by name on Wuzzuf and LinkedIn and on their own career sites.
- Target companies (`core/career/companies.json`) go first in the review: Big 4,
  then top employers in Egypt. Add a company there with its aliases.
- Closed postings ("No longer accepting applications", filled, expired, 404) are
  skipped before scoring (`core/career/liveness.py`, career-ops' patterns), so
  no Claude call is spent on a job that can't be applied to.

## A CV tailored to one job (made with Mo in Claude Code)

For the jobs worth a real shot, Mo opens Claude Code in this repo and says
"tailor my CV for <company>". It runs on his subscription, so it costs the API
budget nothing.

1. Read the posting (`description` in `data/career/applications.json`) and
   Mo's master CV, `Downloads\Mohab Mohamed Fawzy CV (AI-Data-BA).docx` (the
   ERP one for ERP roles).
2. Copy the .docx with python-docx: reorder skills and projects so what the
   posting asks for comes first, reword bullets in its terms. Only facts the
   CV already states; keep it to one page. Same for a letter if wanted.
3. Save it as PDF through Word (`win32com`, `SaveAs(..., FileFormat=17)`) as
   `Downloads\Mohab Mohamed Fawzy CV (<Company> <Role>).pdf`. Mo reads it.
4. Attach it to the application:
   `tracker.update(app_id, cv_path=r"<pdf>", draft={**app["draft"], "body": letter})`.

That file is then the one sent (`profile.for_job` prefers the application's
own `cv_path` to the main or ERP CV), and the review page shows "CV: <file>"
next to the job so Mo sees which one goes before he presses Send.

## How applications go out

| Channel  | When                              | What happens |
|----------|-----------------------------------|--------------|
| email    | the posting gives an HR address   | Gmail from Mo's account, CV attached |
| wuzzuf   | Wuzzuf posting                    | applied in Mo's signed-in Comet, tab closed after |
| linkedin | LinkedIn posting                  | Easy Apply, or its Apply button through to the employer's form, in Comet; max `linkedin_daily_cap`/day |
| site     | anything else (company sites)     | the employer's form filled in **and submitted** in Comet |

Mo ticking an application and pressing Send in the review is the go-ahead:
El Fager fills every field, uploads the CV, pastes the letter, goes page by page
and submits. Each form step is decided by Claude Code on his subscription (the
API when it can't), from a screenshot plus the page's own field list, several
fields a step (`core/agents/browser_agent.py`). A tab the Apply button opens is
followed. Live test on a 2-page form: 7 steps, 45 s, every field right.

When it can't finish, the application waits as `needs_you` and the job-hunt
line says why:
- **A question the facts don't answer** ("Do you hold a driving licence?"):
  El Fager asks Mo; his answer is saved word for word for every form after
  (`extra_answers`), and the application is retried by itself.
- **A site that needs an account** or a sign-in: Mo makes it once in Comet, then
  "retry my applications".
- The question also shows on the review page ("A form needs your answer") with a
  box to answer it from his phone.
- **On WhatsApp** (`core/ask_mo.py`): the question, or the sign-in a site wants,
  goes to Mo's WhatsApp; he replies there ("Yes", "done", or "skip") and El Fager
  reads the reply from Twilio within a minute, day or night, saves it and sends
  the application again. One question on his phone at a time. The same channel
  carries everything else that waits on him: a stuck mission (his reply says how
  to go on, or "stop"), and any background task's question (the brain's `ask_mo`
  tool; his answer comes back to it as a new task).

"Application status" ends with what the hunt leans on: Claude Code (ready, or
why its last call failed), Gmail, and Comet (connected, or "will close and
reopen once when you press Send"; Send says so too). "What should I learn" is
worked out by the nightly hunt and answered at once. With Claude Code the hunt
scores four times the daily target (twice on the API): a night of 10 went from
3 ready to 9.

Browser applications are spaced 45–120 s apart. Every send goes to the Trust
Ledger.

## Guard rails

- **Only Mo approves:** `approve_applications` is refused in background turns,
  so a background run can prepare a batch but never send it.
- **Nothing invented:** scores, letters and form answers use only the CV and the
  answers Mo gave.
- **Cost:** scoring, letters, interview prep and referral notes use
  `claude-sonnet-5`, and `claude-opus-5` when they're for the Big 4 or one of
  Mo's picks above (`core/career/claude.py`) -- except letters, which get Opus
  for the Big 4 only. **Every question goes to Claude Code first**
  (`claude.exe -p`, stripped to a bare model call, without the API key), so it
  runs on Mo's Claude subscription and costs the $10 API budget nothing; a run of
  10 takes minutes. The API answers only what Claude Code can't (not installed,
  logged out, over the plan's limit, wrong-shaped answer): as Message Batches at
  half price, about $0.40 for 10. Telemetry marks subscription calls
  `"subscription": true` at $0. Nothing is
  scored or drafted until the CV is imported; programme deadlines are still
  checked. Logged under "career" in telemetry; the usage audit shows the spend.

## Settings

"Show my application settings" / "set my daily target to 60" / "set the
minimum score to 70". Stored in `data/career/settings.json`.
