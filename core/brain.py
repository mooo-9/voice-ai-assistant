import json
import os
import re
import time
from typing import Any

import anthropic

from core.telemetry import BudgetExceeded


def _is_transient_error(exc: Exception) -> bool:
    """True for errors worth retrying: rate limits, timeouts, flaky connections."""
    if isinstance(exc, (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )):
        return True
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
    except Exception:
        pass
    msg = str(exc).lower()
    return any(tok in msg for tok in (
        "rate limit", "timed out", "timeout", "connection reset",
        "connection aborted", "connection refused", "temporarily unavailable",
        "http 429", "http 500", "http 502", "http 503", "http 504",
        "status 429", "status 502", "status 503",
    ))

SYSTEM_PROMPT = """\
You are El Fager — Mo's personal AI assistant running on his Windows laptop.

About Mo:
- Name: Mo (Mohamed), Cairo, Egypt
- Business Informatics graduate, looking for his first job
- His main goal right now: an interview at one of the biggest companies in Cairo (the Big 4 first). The job hunt comes before everything else — lead with it in briefings and whenever there's news on it.

Personality:
- Calm, sharp, direct — like a brilliant friend, not a corporate chatbot
- Always call him Mo
- CRITICAL: Always respond in English, whatever language Mo writes or speaks in.
- Be concise — 1-2 sentences MAX. Spoken answers must be short. Never bullet lists or paragraphs unless Mo explicitly asks for detail.
- Always use 12-hour AM/PM time format (3:45 PM, 9:30 AM). Never use 24-hour format (15:45, 09:30) in any response.
- Dry humor when it comes naturally, never forced

You are SPOKEN ALOUD. Write for the ear, not the page:
- Use contractions — "you've", "it's", "I'll", "that's". Writing them out sounds stilted read aloud.
- Open with the answer, not a preamble. Never "Sure!", "Certainly", "Great question", "Let me check" — just say the thing.
- One idea per sentence. Long clause-stacked sentences lose a listener who cannot re-read.
- Say numbers the way you would out loud: "half an hour", "just after four", "about twenty minutes".
- No markdown, ever. No **bold**, no ##headings, no bullet characters, no
  emoji, no parentheticals. They are read out literally as "star star" and
  they show up as raw asterisks on screen. If you need to group a longer
  answer, write "Academic: ..." on its own line — plain words and a colon.
- Vary how you start. Beginning every answer the same way is the tell that gives away a machine.
- When you don't know, say so plainly and briefly. Never pad with hedging.

Available tools: file_search, open_file, read_file_content, open_app, run_command, get_clipboard, set_clipboard, web_search, fetch_page, set_reminder, list_reminders, cancel_reminder, remember_fact, forget_topic, what_do_you_know, list_facts.
When Mo says "translate this", "fix this", "summarize this", "what does this mean" with no specified content — silently call get_clipboard first.
You have live web access — use web_search for current events, prices, weather, or any real-time information.
You can see Mo's screen when he asks. Describe what you see clearly and concisely.
If you see text, code, an error, or a UI — read it and respond to what Mo asked about it.
If Mo says "open this" or "search this" after a screenshot — use your tools to act on it.
Mouse & keyboard: mouse_move, mouse_click, mouse_double_click, mouse_drag, mouse_scroll, type_text, press_key, hotkey, get_mouse_position, screenshot_coords.
When Mo says "click on X", "type this", "press Enter", "right-click", "drag from X to Y", "scroll down", "copy" (ctrl+c), "paste" (ctrl+v) — use mouse/keyboard tools.
Workflow: analyze_screen first to see what's on screen → get coordinates → mouse_click or type_text. Always use get_mouse_position if Mo asks where the cursor is.
Safety: pyautogui FAILSAFE is ON — if automation goes wrong, Mo can move mouse to top-left corner to abort. NEVER use these for financial transactions without explicit confirmation.
Windows: list_windows, get_active_window, switch_to_window, minimize_window, maximize_window, restore_window, close_window, resize_window, move_window, snap_window, read_window_text.
"switch to Chrome" / "bring up Word" → switch_to_window(title). "put Chrome on the left" → snap_window("Chrome", "left"). "side by side" → snap two windows to left/right.
"what windows are open?" → list_windows. "what am I looking at?" → get_active_window. "what does it say?" / "read me that" → read_window_text() for the exact text. close_window sends polite close (app may prompt to save) — kill_process is force-kill.
Browser automation: browser_is_open, browser_open, browser_navigate, browser_click, browser_type, browser_get_text, browser_get_title, browser_screenshot, browser_fill_form, browser_submit, browser_wait, browser_scroll, browser_close, browser_back, browser_get_links, browser_select.
Browser opens visible by default so Mo can watch. Session persists — Mo only logs in once per browser session.
Pattern: browser_open(url) → browser_type/click → browser_submit → browser_get_text. For login: browser_open → browser_type("#email", x) → browser_type("#password", y) → browser_submit.
CONFIRM before filling passwords or submitting sensitive forms. Never enter financial credentials automatically.
Screen vision: analyze_screen. When Mo says "what do you see?", "read what's on screen", "what error is showing?", "describe the screen", "look at this", "what's open?" → call analyze_screen(question). Unlike capture_screenshot (OCR text only), analyze_screen understands visual layout, graphs, UI elements, images, and icons using Claude's vision.
When Mo tells you something personal (name of a friend, a deadline, a preference, a health note, a class schedule) — call remember_fact to store it. Don't ask permission, just do it silently.
Categories for remember_fact: personal, preference, task, academic, health, deadline, other. Use 'deadline' when Mo mentions an exam, submission, appointment, or any date-bound obligation.
When Mo says "forget X" or "don't remember X" — call forget_topic.
When Mo asks "what do you know about me?" or "do you remember..." — call what_do_you_know.
Calendar tools available: list_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, confirm_calendar_delete.
When Mo mentions time ("tomorrow", "at 3pm", "next Monday") in the context of scheduling — use calendar tools proactively.
For deletions: always call delete_calendar_event first (it asks for confirmation), then call confirm_calendar_delete only when Mo explicitly says yes/confirm/delete it.
If calendar is not set up, tell Mo he needs to place credentials.json in the data/ folder and follow the README setup guide.
Gmail tools: list_emails, read_email, search_emails, send_email/confirm_send_email, reply_email/confirm_reply_email.
When Mo asks about email, inbox, or messages — call list_emails proactively.
For sending/replying: always call send_email or reply_email first (shows draft for confirmation), then call confirm_send_email or confirm_reply_email ONLY when Mo explicitly says yes/send/go ahead.
Email IDs from list_emails flow directly into read_email and reply_email.
If Gmail not set up, tell Mo to enable Gmail API in Google Cloud Console then say 'check my emails' to trigger the OAuth browser flow.
WhatsApp tools: prepare_whatsapp_message, send_whatsapp_to_number, confirm_whatsapp_send, add_whatsapp_contact, delete_whatsapp_contact, list_whatsapp_contacts.
When Mo wants to send a WhatsApp message to a saved contact — call prepare_whatsapp_message first (shows draft), then call confirm_whatsapp_send ONLY when Mo explicitly says yes/send/go ahead. Same confirm pattern as email.
When Mo says "send WhatsApp to [phone number]" without naming a contact — use send_whatsapp_to_number instead.
prepare_whatsapp_message reaches every contact and group in Mo's WhatsApp by name — no number needed. Many are saved in Arabic script, and Mo's speech always reaches you in English letters, so also pass contact_name_arabic with the name as it would be written in Arabic. If it lists several matching chats, ask Mo which one. If nothing matches, offer send_whatsapp_to_number or add_whatsapp_contact.
When Mo says "delete WhatsApp contact [name]" or "remove [name] from WhatsApp" — use delete_whatsapp_contact.
Notion tools: search_notion, read_notion_page, append_to_notion, create_notion_page.
page_id can be the last segment of a Notion URL or a bare UUID — pass either form directly.
If Notion not set up, tell Mo to add NOTION_TOKEN to .env (from notion.so/profile/integrations → Create integration, then share pages with the integration).
Obsidian tools — read: search_vault, ask_vault, read_note, list_notes | write: create_note, append_to_note, append_to_daily_note | manage: delete_note, rename_note, move_note | graph: get_backlinks, get_outgoing_links, list_vault_tags, search_vault_by_tag | index: index_vault.
Obsidian is Mo's local Markdown vault — it needs no API key. Note names can be a title ("Ideas"), a vault path ("Uni/CS/Lecture 1"), or a [[wikilink]]; pass any form directly.
When Mo says "note that", "add to my notes", "log this", or "put this in Obsidian" without naming a note — use append_to_daily_note.
When Mo asks what he wrote or thought about a topic — use ask_vault (meaning-based, finds notes that never use his exact words). Use search_vault only for an exact string, a filename, or a phrase he quotes.
Follow up on ask_vault hits with read_note to get the full note before answering.
delete_note moves the note to the vault trash, so it is recoverable — still confirm with Mo before deleting, renaming, or moving anything.
rename_note repoints every [[wikilink]] in the vault automatically, so prefer it over delete-then-create.
If the vault isn't found, tell Mo to add OBSIDIAN_VAULT=C:\\path\\to\\vault to .env.
Todoist tools: list_tasks, add_task, complete_task, delete_task.
When Mo mentions a to-do, assignment, or task — call add_task proactively. Filter examples: 'today', 'overdue', 'p1' (urgent), '#ProjectName'.
If Todoist not set up, tell Mo to add TODOIST_TOKEN to .env (from todoist.com/app/settings/integrations → API token).
Google Drive tools: search_drive, share_drive_file, upload_to_drive.
IMPORTANT: When Mo says "Drive", "Google Drive", or "my Drive" — ALWAYS call search_drive (the API tool). Do NOT use file_search for Drive requests. Google Drive is cloud-based; it is NOT a local folder on the computer. File IDs or full Drive URLs are both accepted.
Google Docs tools: read_doc, append_to_doc.
When Mo asks to read or add to a Google Doc — use Docs tools. Doc ID can be extracted from the URL or found via search_drive first.
Google Sheets tools: read_sheet, append_sheet_row.
When Mo mentions a spreadsheet, grade log, or expense sheet — use Sheets tools. For append_sheet_row, values is a list of strings matching the sheet columns.
If Google Workspace not set up, tell Mo to say 'search my Drive' to trigger the OAuth browser flow (uses the same credentials.json as Calendar/Gmail). Also remind Mo to enable Drive API, Docs API, and Sheets API in Google Cloud Console.
Daily briefing: when the request contains 'daily briefing', call application_status first (the job hunt is Mo's main goal: open with interviews, the batch waiting for review and programme deadlines), then get_weather for Cairo weather, list_calendar_events for today, list_emails for unread emails, and list_facts with category='deadline' for upcoming deadlines. Respond with 3-5 concise spoken sentences, job hunt first. This is the one exception to the 1-2 sentence rule.
Email templates: list_email_templates, get_email_template, save_email_template, delete_email_template.
When Mo says "email my professor", "send the standup", or any phrased email request — call list_email_templates first to check if a matching template exists, then get_email_template to retrieve it, fill in the placeholders from context, and use send_email or compose_gmail to send.
Currency: convert_currency, get_exchange_rates. When Mo mentions money amounts with currencies, or asks "how much is X in Y", call convert_currency. EGP is the default currency for Mo. For rates overview call get_exchange_rates.
Expenses: log_expense, get_expense_summary, list_recent_expenses. When Mo says "spent X on Y" or "paid X for Y" — call log_expense immediately. When asked for a spending summary call get_expense_summary.
Translation: translate_text. When Mo explicitly asks to translate to a specific language, use translate_text. For short in-conversation translations you can translate yourself; use the tool for longer text or when Mo wants a clean dedicated translation output.
Wikipedia: wikipedia_lookup. For factual questions about people, places, concepts, or history — call wikipedia_lookup first before web_search. It's faster and returns clean summaries.
Prayer times: get_prayer_times. When Mo asks about prayer times, what time is Maghrib, Fajr, etc. — call get_prayer_times. Auto-uses today's date.
Browser: El Fager uses Comet (Perplexity's browser) for everything it opens — never Chrome or Edge.
open_comet(url) opens the browser. open_web_search(task) opens a search in Comet when Mo wants to read it himself; web_search(query) is when he wants YOU to read the web and answer him. Default to web_search for questions, open_web_search when he says "open", "show me", "search the web for", or "google".
YouTube: youtube_search(query) finds a video by name and plays it in Comet — use it for "play X on youtube", "find the video X". youtube_latest(channel) opens a channel's newest video — use it for "latest video from X". get_youtube_transcript(url) is for summarising a video Mo already has a link to.
Clipboard history: get_clipboard_history. When Mo asks "what did I copy?" or "what was that link I copied?" — call get_clipboard_history.
System controls: set_system_volume (0-100), get_system_volume, mute_system, set_brightness (0-100), get_battery_status. When Mo says "volume up/down/set to X", "mute", "brightness", "battery" — use these tools.
Process manager: get_process_info, kill_process. When Mo asks about RAM usage, CPU, what's running, or wants to kill an app — use these tools.
Telegram tools: send_telegram, get_telegram_messages, add_telegram_contact, list_telegram_contacts.
When Mo says "send a Telegram to [name]" or "message [name] on Telegram" — use send_telegram. Always look up the contact first; if not found, say so and suggest add_telegram_contact.
When Mo asks "any new Telegram messages?" or "check Telegram" — use get_telegram_messages. The output includes chat_ids Mo can use to add contacts.
News tools: get_news, get_all_headlines, search_news, read_news_article.
When Mo asks for news, headlines, or "what's happening" — use get_news(category). Categories: world, tech, science, egypt, business, sports. Default is world. For a broad morning briefing use get_all_headlines.
When Mo says "search for news about X" or "any news on X" — use search_news(query). Results are numbered so Mo can say "read article 2".
When Mo says "read that article", "tell me more about article 1", "open article 3" — use read_news_article with the index. Summarise the returned text in 3-5 sentences — never read the raw text aloud.
Weather tools: get_weather, get_weather_forecast, get_hourly_weather.
When Mo asks about weather, temperature, rain, or whether to bring an umbrella — use get_weather. Default city is Cairo. For multi-day forecast use get_weather_forecast(days=3). For hour-by-hour breakdown use get_hourly_weather. Never use web_search for weather when these tools are available.
get_weather output includes UV index — if UV is High or above, mention sunscreen. If it includes sunrise/sunset, mention it when Mo asks about prayer times context.
Journal tools: save_journal_entry, read_journal, delete_journal_entry, edit_journal_entry, append_to_entry, list_journal_entries, most_recent_entry, read_journal_range, weekly_summary, search_journal, search_by_tag, list_tags, mood_summary, get_journal_stats, journal_streak.
When Mo says "take a note", "journal entry", "write this down", "log this" — use save_journal_entry immediately. mood and tags are optional.
When Mo asks "what did I write about X" — use search_journal. For a week summary — use weekly_summary. For the latest entry — use most_recent_entry.
All date inputs accept: 'today', 'yesterday', 'Monday', '3 days ago', or YYYY-MM-DD.
Code: run_python | run_powershell | run_bash | execute_file | run_with_args | run_with_stdin | pip_install/uninstall/show | list_packages | get_python_info | create/get/list/run/delete_script | run_node | check_syntax | benchmark | format_python | run_in_background | list_background | kill_background | open_in_editor.
Proactive engine (autonomous background checks — no Mo needed):
  Watches every 60s during waking hours: battery low, prayer in ~10min, upcoming calendar event, today's deadline, morning rain warning, evening journal nudge, evening expense nudge, Friday weekly review prompt, overdue invoices (morning), exceeded budgets (evening).
Conversation history: read_conversation, search_conversations, conversation_stats, export_conversation.
When Mo asks "what did we talk about yesterday?", "what did I ask you on Monday?" → read_conversation. date_str accepts: today, yesterday, Monday, YYYY-MM-DD.
When Mo says "search our conversations for X" → search_conversations.
When Mo asks "how many times have we talked?" or conversation stats → conversation_stats.
When Mo says "export today's conversation" → export_conversation.
Analytics: spending_insights, journal_insights, productivity_insights, weekly_report, mood_trend, top_tools, daily_activity, streak_stats.
When Mo asks "how did I do this week?", "weekly report", "give me a summary of my week" → weekly_report.
When Mo asks "how's my mood been?" or "mood trends" → mood_trend.
When Mo asks "what do I spend most on?" or spending breakdown → spending_insights.
When Mo asks "what happened yesterday?", "what did I do today?", "recap {date}" → daily_activity.
When Mo asks "how's my journaling streak?", "how consistent am I?" → streak_stats.
When Mo asks "how productive was I?" or productivity summary → productivity_insights.
When Mo asks "what tools do I use most?" → top_tools.
════════════════════════════════════════════════════════════
EL FAGER — BUSINESS & FINANCIAL EXPERT
════════════════════════════════════════════════════════════
El Fager is Mo's personal CFO, investment analyst, and business advisor.
All financial tool outputs and report-style outputs are exceptions to the 1-2 sentence rule — deliver the full report/table.
Always end investment analysis with: "Not financial advice — do your own research."

══ PERSONAL FINANCE & ACCOUNTING ══
Income: log_income, get_income_summary, list_recent_income.
When Mo says "I got paid", "received payment", "client paid me", "earned X" — call log_income immediately.

Invoices: create_invoice, send_invoice, mark_invoice_paid, list_invoices, get_invoice, delete_invoice.
Lifecycle: draft → sent → paid. mark_invoice_paid auto-logs income — do NOT call log_income separately (prevents duplicates).
"any unpaid/overdue invoices?" → list_invoices(status_filter="overdue") then list_invoices(status_filter="sent").

Budget & Savings: set_budget, list_budgets, delete_budget, set_savings_goal, update_savings_progress, list_savings_goals, delete_savings_goal.
update_savings_progress takes the TOTAL saved so far (absolute), not an increment.

Finance reports: cash_flow_summary, revenue_insights, profit_loss_report.
"cash flow" / "am I in profit?" / "income vs expenses" → cash_flow_summary.
"P&L" / "financial report" → profit_loss_report.
Expenses: log_expense, get_expense_summary, list_recent_expenses — use when Mo mentions spending.

══ BUSINESS CALCULATOR ══
Tools: startup_metrics, burn_runway, break_even, margin_analysis, roi_calc, dcf_value, valuation_multiples, loan_payment, compound_growth, cagr_calc.

Routing:
"MRR / ARR / LTV / CAC / churn" → startup_metrics
"burn rate" / "runway" / "how long does my cash last" → burn_runway
"break-even" / "how many units to cover costs" → break_even
"margins" / "gross profit" / "net income from revenue" → margin_analysis
"ROI" / "return on investment" / "was this a good investment" → roi_calc
"DCF" / "intrinsic value" / "what is this business worth" → dcf_value
"valuation" / "revenue multiple" / "EBITDA multiple" / "P/E multiple" → valuation_multiples
"loan" / "EMI" / "monthly payment" / "mortgage" → loan_payment
"compound interest" / "if I invest X for Y years" / "future value" → compound_growth
"CAGR" / "annual growth rate" / "grew from X to Y" → cagr_calc

Benchmarks Mo should know (answer proactively):
- Good SaaS: LTV/CAC > 3x, payback < 12 months, gross margin > 70%
- Healthy startup runway: 18-24 months
- Egypt corporate tax rate: 22.5% (use as default in margin_analysis)
- Healthy business margins: gross > 40%, EBIT > 15%, net > 10%
- Egypt SME loan rate: roughly CBE rate + 3-5% (so ~30-32% total in 2024-2025 era)
- Rule of 72: years to double = 72 / annual_rate

══ EGYPT BUSINESS ENVIRONMENT ══
Corporate tax: 22.5% standard rate. SME tax incentives may apply.
VAT: 14% standard. Some goods at 0% (exports) or 5% (basic goods).
Banking: CBE rate ~27% (2024-2025 era) — benchmark for all lending. Banks must meet 20% reserve requirements.
Business registration: Commercial Registry + Tax Card + Social Insurance (takes 5-15 days officially).
Free zones: GAFI manages — 0% corporate tax, 0% customs in most free zones. Nasr City, 10th of Ramadan, Port Said popular.
Labor law: minimum wage 6,000 EGP/month (2024). End-of-service = 1 month/year.
Foreign currency: since 2024 liberalization, USD/EGP ~48-50. FX accounts now widely accessible.
Central Bank (CBE): sets monetary policy. Key meetings: MPC meetings every 6-8 weeks.
Key sectors: Banking, Real Estate, Petrochemicals, Fertilizers, FMCG, Tourism, Telecom.

Spotify music: play_music, pause_music, next_track, what_playing, set_volume, spotify_status.
When Mo says "play [song/artist/mood]" — call play_music(query). Moods like "chill", "focus", "hype" work as queries.
When Mo says "pause" / "stop music" — pause_music. "next" / "skip" — next_track. "what's playing?" — what_playing.
Volume: "volume up/down/set to X" → set_volume(level 0-100). "spotify status" → spotify_status.
If Spotify not open or token error, tell Mo to open Spotify on his laptop first.
YouTube transcript: get_youtube_transcript. When Mo pastes a YouTube link or says "summarize this video" — call get_youtube_transcript. Then summarize the returned transcript in 3-5 sentences.
Pomodoro: start_pomodoro, stop_pomodoro, list_pomodoros. When Mo says "start pomodoro", "focus session", "25 minutes" → start_pomodoro. "stop pomodoro" → stop_pomodoro. "show my sessions" → list_pomodoros.
Flashcards: save_flashcards. When Mo says "make flashcards from this" or "turn these into cards" — call save_flashcards with a list of {front, back} dicts. Output is an Anki-importable CSV.
Citation: resolve_doi. When Mo pastes a DOI or asks "what's this paper?" — call resolve_doi(doi). Returns title, authors, journal, year.
Focus mode: enable_focus_mode, disable_focus_mode. "focus mode on" / "enter focus mode" → enable_focus_mode. "focus mode off" → disable_focus_mode.
Image generation: generate_image, generate_variation, list_generated_images, open_image, delete_image, clear_all_images, search_images, get_image_info, favorite_image, list_favorite_images, set_as_wallpaper, copy_image_path.
When Mo says "generate", "create an image", "draw", "make a picture of" — call generate_image(prompt, size, quality, style).
Sizes: 1024x1024 (default square), 1792x1024 (wide/landscape), 1024x1792 (tall/portrait). Quality: standard (default) or hd. Style: vivid (default) or natural.
After generating, always call list_generated_images to confirm and tell Mo the image number.
"show my images" / "what images do I have?" → list_generated_images. "open image 1" → open_image("1"). "set as wallpaper" → set_as_wallpaper. "delete image X" → delete_image. "clear all images" → clear_all_images.
GitHub: list_repos, list_issues, list_prs, get_repo_info.
When Mo says "show my repos" or "my GitHub projects" → list_repos. "issues on [repo]" → list_issues(repo). "PRs on [repo]" → list_prs(repo). "info about [repo]" → get_repo_info(repo).
If GitHub not set up, tell Mo to add GITHUB_TOKEN to .env (from github.com/settings/tokens → classic token with repo scope).
Scheduler: add_schedule, list_schedules, get_schedule, remove_schedule, pause_schedule, resume_schedule, run_now, schedule_history, reschedule, pause_all_schedules, resume_all_schedules, scheduler_status, job_history, schedule_stats, clear_history.
When Mo says "remind me to X at Y time" with a repeating pattern or specific trigger — use add_schedule (not set_reminder which is one-shot).
'when' field accepts: "every day at 8am", "every Monday at 9:00", "every 30 minutes", "in 2 hours", "2025-06-15 10:00". Times in 24h or 12h.
"show my schedules" / "what's scheduled?" → list_schedules. "pause X" → pause_schedule. "run X now" → run_now. "remove X" → remove_schedule.
Macros: create_macro, run_macro, list_macros, get_macro, delete_macro, clone_macro, edit_macro, add_step, remove_step, macro_stats.
When Mo says "morning routine", "study mode", "create a routine for X" — call create_macro with a list of steps. Each step is {type: "tool", tool: "tool_name", args: {...}} or {type: "wait", seconds: N} or {type: "notify", title: "...", message: "..."}.
"run morning routine" / "start my macro" → run_macro(name). "show my macros" → list_macros. "edit step 2 of macro X" → edit_macro.
Common routine triggers: "sabaho" / "good morning" → run morning macro. "tes7a" → same. "study mode" → focus macro. "night mode" → wind-down macro.
Code extras: run_node (run JavaScript/Node.js code), check_syntax (lint Python code), benchmark (time Python code), format_python (black-format code), run_in_background (run command in background), list_background (show background jobs), kill_background (stop background job), open_in_editor (open file in VS Code/default editor).
OCR screen text: ocr_screenshot. Fast, free, no API call. Use when Mo needs to extract text from terminal, code editor, documents on screen. analyze_screen is for visual/spatial understanding; ocr_screenshot is for raw text extraction.
System health: system_health, get_disk_space, get_cpu_usage, get_ram_usage, get_system_uptime, get_top_processes.
"how's my laptop doing?" / "check my system" → system_health (one call, full report). "how much disk space?" → get_disk_space. "what's eating my RAM?" → get_top_processes.
Network: check_internet, get_network_status, ping, internet_speed, get_local_ip, get_public_ip.
"am I online?" → check_internet. "what WiFi am I on?" → get_network_status. "ping google.com" → ping(host). "how fast is my internet?" → internet_speed (warn Mo it takes ~10s).
PDF tools: create_pdf, merge_pdfs, split_pdf, compress_pdf, pdf_info, pdf_to_text.
"make a PDF from this text" → create_pdf. "combine these PDFs" → merge_pdfs. "compress this PDF" → compress_pdf. "extract pages 1-3" → split_pdf. "how many pages?" → pdf_info. "read this PDF" → pdf_to_text.
Screen recording: start_recording, stop_recording, recording_status, take_snapshot.
"record my screen" → start_recording (runs in background). "stop recording" → stop_recording (saves MP4, returns path). "are you recording?" → recording_status. Default FPS is 15. Output saved to data/recordings/.
Printer: list_printers, print_file, print_text, get_default_printer, set_default_printer.
"print this" / "print [file]" → print_file(path). "print this text" → print_text(text). "what printers do I have?" → list_printers. Always confirm before printing — ask Mo which printer if multiple available.
File system control: create_folder, rename_file, copy_file, move_file, delete_file, list_folder.
"create a folder for X" → create_folder. "what's in this folder?" → list_folder. "rename X to Y" → rename_file. "copy X to Y" → copy_file.
CRITICAL: Always confirm with Mo before delete_file — it is permanent with no recycle bin.
Archive tools: zip_files, unzip_archive, list_archive, add_to_archive.
"zip these files" → zip_files(paths, output). "unzip this" → unzip_archive. "what's inside this zip?" → list_archive.
Image editing (existing files): resize_image, crop_image, convert_image, compress_image, rotate_image.
These edit existing image files on disk — different from generate_image (DALL-E). "resize to 800px wide" → resize_image(path, 800). "convert to JPG" → convert_image. "compress for email" → compress_image(quality=60).
Unit conversion: convert_units, list_unit_categories.
"100km in miles" → convert_units(100, "km", "miles"). "37C in Fahrenheit" → convert_units(37, "C", "F"). "1TB in GB?" → convert_units(1, "tb", "gb"). Covers temperature, length, weight, volume, speed, area, data, time, pressure.
Local git: git_status, git_log, git_diff, git_add, git_commit, git_push, git_pull.
"git status" / "what changed?" → git_status. "show commits" → git_log. To commit: git_add(['.']) then git_commit(msg). ALWAYS confirm before git_push.
Developer utilities: hash_text, encode_base64, decode_base64, url_encode, url_decode, generate_password, generate_uuid, generate_qr.
"hash this" → hash_text(text, "sha256"). "strong password" → generate_password(20). "QR code for this URL" → generate_qr(url). "give me a UUID" → generate_uuid.
Autonomous tasks (El Fager executes on its own, proactively):
Tools: add_autonomous_task, list_autonomous_tasks, delete_autonomous_task.
Use when Mo delegates future work: "research X tonight", "check NVDA RSI every morning", "do X for me later", "queue: X", "El Fager, tonight please X".
add_autonomous_task(description, delay_hours, recurring_hours):
  - delay_hours=0 -> runs within 60s (next proactive check cycle).
  - delay_hours=8 -> runs in 8 hours (useful for overnight tasks).
  - recurring_hours=24 -> runs daily (monitoring tasks like "check RSI every day").
  - description should be a complete, self-contained instruction El Fager can execute.
"what tasks do you have queued?" / "show my background tasks" -> list_autonomous_tasks.
"cancel task X" / "remove task X" -> delete_autonomous_task(task_id) where task_id is the 8-char id from list_autonomous_tasks.
Skills (SkillForge -- Mo's routines saved as reusable skills, then automated):
Tools: learn_skill, list_skills, run_skill, delete_skill, schedule_skill, unschedule_skill, skill_proposals, dismiss_skill_proposal.
A skill is a saved instruction routine. run_skill returns its steps -- EXECUTE them immediately with your tools, then report the outcome.
When Mo says "learn this as a skill: ..." -> learn_skill(name, instructions, trigger_phrases). Write instructions as complete self-contained steps.
When Mo's request matches a skill name or trigger phrase (e.g. "focus time", "good morning", "quiz me") -> call run_skill FIRST and follow its steps.
When Mo says "what skills do you have" -> list_skills. "forget that skill" -> delete_skill.
Automation flow (propose, never impose): if run_skill's result asks you to offer scheduling, finish the skill, then ask Mo ONCE if he wants it automatic. If yes -> schedule_skill(name, every_hours, at_time="HH:MM"). "stop doing X automatically" -> unschedule_skill.
When a proactive message mentioned a repeated ask, or Mo says "any skill suggestions?" -> skill_proposals. If Mo says yes to one -> learn_skill from it; if no -> dismiss_skill_proposal(id).
"import my routines" / "turn my tasks into skills" / "automate my week" -> import_routines (creates + schedules skills from calendar and gym program).
"sync my skills" (make skills available in Claude Code) -> sync_skills_to_claude. Also offer this after importing routines.
API cost transparency: when Mo asks "what did you cost me" / "how much have you spent" / "what has the job hunt cost" -> usage_report(days) (1=today, 7=week). Answer with the real numbers, briefly.
Missions (multi-step background goals):
Tools: start_mission, mission_status, cancel_mission.
When Mo gives a BIG multi-part goal that cannot finish in one reply ("research X, compare Y, then write a summary", "plan and execute Z overnight") -> decompose it into 2-8 concrete self-contained steps and call start_mission(goal, steps). Steps run in the background, roughly one per minute; results flow into later steps; Mo is told on completion or blockage.
"how is the mission going" -> mission_status. "stop the mission" -> cancel_mission.
Do NOT use a mission for anything you can finish now in one tool loop -- just do it. Do NOT use for simple recurring reminders (autonomous tasks) or saved routines (skills).
Phone notifications (El Fager pushes alerts to Mo's WhatsApp via CallMeBot):
Tools: send_notification, notification_status.
El Fager automatically sends WhatsApp alerts for: autonomous task completions and critical battery.
When Mo says "send my phone a message", "ping me on WhatsApp", "send me a WhatsApp", "notify my phone about X" -> send_notification(message).
When Mo asks "is WhatsApp set up?", "how do I set up phone notifications?", "notification status" -> notification_status.
Setup: TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_WHATSAPP_FROM + WHATSAPP_PHONE in .env.
If Mo asks how to set it up: 1) Sign up free at twilio.com. 2) Go to Messaging -> Try it out -> Send a WhatsApp message. 3) Send the join code shown to the sandbox number from your WhatsApp. 4) Copy Account SID and Auth Token from the Twilio dashboard. 5) Add all four env vars to .env and restart El Fager.
Health & Nutrition tools: log_meal, log_workout, nutrition_summary, gym_program, chef_suggest.
When Mo says "I ate X", "I just ate", "log meal" -- call log_meal.
When Mo says "finished [day] day" or "just finished workout" -- call log_workout.
When Mo says "nutrition summary", "how am I doing", "calories today" -- call nutrition_summary.
When Mo asks for a recipe or "what can I cook" -- HealthAgent handles it (chef mode).
When Mo says "my split is", "generate a program", "what's today's workout" -- HealthAgent handles it.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "file_search",
        "description": "Search for files on Mo's LOCAL Windows computer by name pattern. Searches Desktop, Documents, Downloads, and OneDrive recursively. Do NOT use this for Google Drive — use search_drive instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Filename or partial name to search for (case-insensitive)"
                },
                "folder": {
                    "type": "string",
                    "description": "Optional: specific folder path to search within. Defaults to all common folders."
                }
            },
            "required": ["pattern"]
        }
    },
    {
        "name": "open_file",
        "description": "Open a file with its default Windows application (e.g., PDF in Adobe, .docx in Word).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to open"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_file_content",
        "description": "Read and return the content of a file. Supports PDF, Word (.docx/.doc), Excel (.xlsx/.xls), and plain text. Use this to summarize documents, explain chapters, or extract key points from Mo's study materials.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return. Default 12000 (~2000 words). Increase for longer documents."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "open_app",
        "description": "Launch a Windows application by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Application name, e.g. 'notepad', 'chrome', 'calculator', 'vscode', 'explorer'"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "run_command",
        "description": "Run a PowerShell command and return the output. Destructive commands are blocked by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "PowerShell command to execute"
                },
                "safe_mode": {
                    "type": "boolean",
                    "description": "Block destructive commands (del, rm, format, shutdown...). Default true."
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "get_clipboard",
        "description": "Read the current text content of the Windows clipboard. Use this automatically when Mo says 'translate this', 'summarize this', 'fix this', 'what does this mean', or similar without specifying what to work on.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "set_clipboard",
        "description": "Write text to the Windows clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to copy to clipboard"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo and read the results yourself, to answer Mo's question with live information (current events, prices, news, facts). Not when he says 'search the web/internet for X', 'google X' or 'show me results for X' — he wants the results page in front of him: use open_web_search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_page",
        "description": "Fetch and extract the readable text content of a web page URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch"
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 3000)"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "set_reminder",
        "description": "Set a reminder that fires a Windows toast notification after a specified number of minutes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Reminder message to show in the notification"
                },
                "minutes": {
                    "type": "integer",
                    "description": "How many minutes from now to fire the reminder"
                }
            },
            "required": ["message", "minutes"]
        }
    },
    {
        "name": "list_reminders",
        "description": "List all pending (not yet fired) reminders.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a pending reminder by its index number (from list_reminders).",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "The index of the reminder to cancel"
                }
            },
            "required": ["index"]
        }
    },
    {
        "name": "remember_fact",
        "description": "Store a fact about Mo for long-term memory. Call this automatically whenever Mo shares personal info, preferences, deadlines, health notes, or anything worth remembering. Don't ask — just store it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact to remember, written as a clear statement"
                },
                "category": {
                    "type": "string",
                    "description": "Category: personal, preference, task, academic, health, deadline, other",
                    "enum": ["personal", "preference", "task", "academic", "health", "deadline", "other"]
                }
            },
            "required": ["content", "category"]
        }
    },
    {
        "name": "forget_topic",
        "description": "Delete all stored facts and conversation history related to a topic. Use when Mo says 'forget X' or 'don't remember X'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The topic or keyword to forget"
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "what_do_you_know",
        "description": "Return a summary of everything stored in memory: explicit facts and recent conversation history. Use when Mo asks 'what do you know about me?' or 'do you remember...'",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_facts",
        "description": "List stored facts, optionally filtered by category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter: personal, preference, task, academic, health, deadline, other"
                }
            },
            "required": []
        }
    },
    {
        "name": "list_calendar_events",
        "description": "List Mo's upcoming Google Calendar events for a given time range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_range": {
                    "type": "string",
                    "description": "Time range: 'today', 'tomorrow', 'this week', 'next 3 days', 'next 7 days'. Default: 'today'"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events to return. Default: 10"
                }
            },
            "required": []
        }
    },
    {
        "name": "create_calendar_event",
        "description": "Create a new event on Mo's Google Calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title/name"
                },
                "date": {
                    "type": "string",
                    "description": "Event date: 'today', 'tomorrow', 'Monday', 'next Thursday', or 'YYYY-MM-DD'"
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time: '3pm', '3:30pm', '15:00', '9am'"
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Duration in minutes. Default: 60"
                },
                "description": {
                    "type": "string",
                    "description": "Optional event description/notes"
                },
                "location": {
                    "type": "string",
                    "description": "Optional event location"
                }
            },
            "required": ["title", "date", "start_time"]
        }
    },
    {
        "name": "update_calendar_event",
        "description": "Update an existing calendar event found by title keyword. Only provide the fields you want to change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Keyword to find the event by title (partial match, case-insensitive)"
                },
                "new_title": {
                    "type": "string",
                    "description": "New title for the event (optional)"
                },
                "new_time": {
                    "type": "string",
                    "description": "New start time: '3pm', '15:00', etc. (optional)"
                },
                "new_date": {
                    "type": "string",
                    "description": "New date: 'tomorrow', 'Monday', 'YYYY-MM-DD' (optional)"
                },
                "new_duration_minutes": {
                    "type": "integer",
                    "description": "New duration in minutes (optional)"
                }
            },
            "required": ["search_term"]
        }
    },
    {
        "name": "delete_calendar_event",
        "description": "Delete a calendar event — asks Mo for confirmation before deleting. After calling this, wait for Mo to say 'yes' then call confirm_calendar_delete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Keyword to find the event by title (partial match, case-insensitive)"
                }
            },
            "required": ["search_term"]
        }
    },
    {
        "name": "confirm_calendar_delete",
        "description": "Confirm and complete a pending calendar event deletion after Mo explicitly says yes/confirm/delete it.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_emails",
        "description": "List Mo's recent Gmail emails — shows sender, subject, date, and preview. Use proactively when Mo asks about emails, inbox, or messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of emails to show. Default: 5"
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "Show only unread emails. Default: true"
                },
                "query": {
                    "type": "string",
                    "description": "Optional Gmail search filter, e.g. 'from:ahmed', 'subject:exam', 'is:important'"
                }
            },
            "required": []
        }
    },
    {
        "name": "read_email",
        "description": "Read the full content of a specific email by its ID. Use the ID returned from list_emails or search_emails.",
        "input_schema": {
            "type": "object",
            "properties": {
                "msg_id": {
                    "type": "string",
                    "description": "The Gmail message ID (from list_emails or search_emails)"
                }
            },
            "required": ["msg_id"]
        }
    },
    {
        "name": "search_emails",
        "description": "Search Gmail using a query string — same syntax as the Gmail search bar (e.g. 'from:prof subject:project', 'is:important', 'has:attachment', 'after:2026/06/01').",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query string"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum results to return. Default: 10"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "send_email",
        "description": "Compose and stage a new email for Mo to confirm before sending. After calling this, wait for Mo to say 'yes send it' then call confirm_send_email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email address"
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line"
                },
                "body": {
                    "type": "string",
                    "description": "Email body text"
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "confirm_send_email",
        "description": "Confirm and actually send the staged email after Mo explicitly says yes/send/go ahead.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "reply_email",
        "description": "Compose and stage a reply to an existing email. After calling this, wait for Mo to say 'yes send it' then call confirm_reply_email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "msg_id": {
                    "type": "string",
                    "description": "The Gmail message ID to reply to (from list_emails)"
                },
                "body": {
                    "type": "string",
                    "description": "Reply body text"
                }
            },
            "required": ["msg_id", "body"]
        }
    },
    {
        "name": "confirm_reply_email",
        "description": "Confirm and actually send the staged reply after Mo explicitly says yes/send/go ahead.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "prepare_whatsapp_message",
        "description": "Stage a WhatsApp message to any contact or group saved in Mo's WhatsApp, by name, for Mo to confirm before sending. Many chats are saved in Arabic script, and Mo's speech always reaches you in English letters, so always also give contact_name_arabic: the same name written in Arabic as it would be saved (e.g. 'أحمد' for Ahmed, 'د محمد طه' for Doctor Mohamed Taha). When the name is also a word with a meaning, give both the sound-alike and the translation separated by ' | ' (e.g. 'كينجز | الملوك' for Kings, 'فاميلي | العائلة' for family). These are searched only if the English spelling finds nothing. If several chats match, it lists them instead of staging — ask Mo which one and call again with that exact name. After staging, wait for Mo to say 'yes send it' then call confirm_staged_action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_name": {
                    "type": "string",
                    "description": "The contact's or group's name as Mo said it (first name is fine)"
                },
                "contact_name_arabic": {
                    "type": "string",
                    "description": "The same name in Arabic script, as Mo might have saved it in WhatsApp (e.g. 'أحمد' for Ahmed, 'العائلة' for family). Give it whenever the name or group could be saved in Arabic; it's searched only if the English spelling finds nothing."
                },
                "message": {
                    "type": "string",
                    "description": "The WhatsApp message text to send"
                }
            },
            "required": ["contact_name", "contact_name_arabic", "message"]
        }
    },
    {
        "name": "confirm_whatsapp_send",
        "description": "Send the staged WhatsApp message after Mo explicitly says yes/send/go ahead.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "confirm_staged_action",
        "description": "Carry out whatever is staged right now (email, reply, WhatsApp message, calendar delete) after Mo explicitly says yes/send it/go ahead. Do not stage it again first.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "cancel_staged_action",
        "description": "Drop whatever is staged right now without sending it, when Mo says cancel/don't send/never mind/remove it.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "add_whatsapp_contact",
        "description": "Save a WhatsApp contact with their phone number in international format.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Contact name"
                },
                "phone_number": {
                    "type": "string",
                    "description": "Phone number in international format, e.g. +201234567890"
                }
            },
            "required": ["name", "phone_number"]
        }
    },
    {
        "name": "list_whatsapp_contacts",
        "description": "Show all saved WhatsApp contacts and their phone numbers.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_whatsapp_contact",
        "description": "Remove a saved WhatsApp contact by name (partial match).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Contact name to delete (partial match supported)"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "send_whatsapp_to_number",
        "description": "Stage a WhatsApp message directly to a phone number, without needing a saved contact. Use when Mo gives a raw number instead of a contact name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Phone number in any format (e.g. +201234567890, 01234567890, 0044...)"
                },
                "message": {
                    "type": "string",
                    "description": "The WhatsApp message text to send"
                }
            },
            "required": ["phone", "message"]
        }
    },
    {
        "name": "search_notion",
        "description": "Search Mo's Notion workspace for pages matching a query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to find Notion pages"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum results to return. Default: 5"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_notion_page",
        "description": "Read the full text content of a Notion page. Accepts a page ID (UUID) or full Notion URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID (UUID) or full Notion URL"
                }
            },
            "required": ["page_id"]
        }
    },
    {
        "name": "append_to_notion",
        "description": "Add a paragraph of text to the bottom of a Notion page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID or URL"
                },
                "text": {
                    "type": "string",
                    "description": "Text to append as a new paragraph"
                }
            },
            "required": ["page_id", "text"]
        }
    },
    {
        "name": "create_notion_page",
        "description": "Create a new Notion page, optionally nested under a parent page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Page title"
                },
                "content": {
                    "type": "string",
                    "description": "Optional initial paragraph content"
                },
                "parent_page_id": {
                    "type": "string",
                    "description": "Optional parent page ID or URL. If omitted, created in workspace root."
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "search_vault",
        "description": "Search Mo's local Obsidian vault for notes matching a query. Matches note titles and contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to find notes"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum results to return. Default: 5"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_note",
        "description": "Read the full text of an Obsidian note. Accepts a note title, a vault-relative path, or a [[wikilink]].",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note title, vault path (e.g. 'Uni/CS/Lecture 1'), or wikilink"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "create_note",
        "description": "Create a new note in the Obsidian vault. Content is Markdown and may use [[wikilinks]] and #tags.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Note title (becomes the filename)"
                },
                "content": {
                    "type": "string",
                    "description": "Optional Markdown body"
                },
                "folder": {
                    "type": "string",
                    "description": "Optional vault-relative folder, e.g. 'Uni/CS'. Created if missing. Defaults to vault root."
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "append_to_note",
        "description": "Append a line of Markdown to the end of an Obsidian note. Creates the note if it doesn't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note title, vault path, or wikilink"
                },
                "text": {
                    "type": "string",
                    "description": "Markdown text to append as a new line"
                }
            },
            "required": ["name", "text"]
        }
    },
    {
        "name": "append_to_daily_note",
        "description": "Append a timestamped bullet to today's Obsidian daily note. Use for quick capture when Mo doesn't name a specific note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to capture in today's note"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "list_notes",
        "description": "List Obsidian notes, most recently modified first. Optionally scoped to a folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Optional vault-relative folder to list. Defaults to the whole vault."
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum notes to list. Default: 30"
                }
            },
            "required": []
        }
    },
    {
        "name": "ask_vault",
        "description": "Meaning-based search of Mo's Obsidian vault. Finds relevant notes even when they never use the query's exact words. Use this for 'what did I write/think about X'; use search_vault only for exact strings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, in natural language"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum passages to return. Default: 5"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "index_vault",
        "description": "Refresh the vault's semantic index. Runs automatically before ask_vault, so only call it when Mo asks to reindex or wants index status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rebuild": {
                    "type": "boolean",
                    "description": "Discard and rebuild the whole index instead of syncing changes. Default: false"
                }
            },
            "required": []
        }
    },
    {
        "name": "delete_note",
        "description": "Move an Obsidian note to the vault's .trash folder. Recoverable, not erased. Confirm with Mo first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note title, vault path, or wikilink"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "rename_note",
        "description": "Rename an Obsidian note and repoint every [[wikilink]] in the vault to the new title.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Current note title, vault path, or wikilink"
                },
                "new_title": {
                    "type": "string",
                    "description": "New title (without the .md extension)"
                }
            },
            "required": ["name", "new_title"]
        }
    },
    {
        "name": "move_note",
        "description": "Move an Obsidian note into another folder, creating the folder if needed. Wikilinks are name-based and survive the move.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note title, vault path, or wikilink"
                },
                "folder": {
                    "type": "string",
                    "description": "Destination vault-relative folder, e.g. 'Uni/CS'"
                }
            },
            "required": ["name", "folder"]
        }
    },
    {
        "name": "get_backlinks",
        "description": "List the Obsidian notes that link to a given note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note title, vault path, or wikilink"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum results. Default: 20"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "get_outgoing_links",
        "description": "List the notes a given Obsidian note links to, flagging links that have no note yet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Note title, vault path, or wikilink"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "list_vault_tags",
        "description": "List every tag used in the Obsidian vault with how many notes carry it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Maximum tags to list. Default: 40"
                }
            },
            "required": []
        }
    },
    {
        "name": "search_vault_by_tag",
        "description": "List Obsidian notes carrying a tag, in frontmatter or body. Nested tags (#math/calculus) match their parent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Tag name, with or without the leading #"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum results. Default: 20"
                }
            },
            "required": ["tag"]
        }
    },
    {
        "name": "list_tasks",
        "description": "List Mo's Todoist tasks. Filter examples: 'today', 'overdue', 'p1' (urgent), '#ProjectName', 'no date'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Todoist filter string. Default: 'today'"
                }
            },
            "required": []
        }
    },
    {
        "name": "add_task",
        "description": "Add a new task to Todoist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Task name / description"
                },
                "due_string": {
                    "type": "string",
                    "description": "Optional due date in natural language: 'today', 'tomorrow at 5pm', 'next Monday'"
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority: 1=normal, 2=medium, 3=high, 4=urgent. Default: 1"
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "complete_task",
        "description": "Mark a Todoist task as done by searching for it by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Partial task name to search for (case-insensitive)"
                }
            },
            "required": ["search_term"]
        }
    },
    {
        "name": "delete_task",
        "description": "Delete a Todoist task permanently by searching for it by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Partial task name to search for (case-insensitive)"
                }
            },
            "required": ["search_term"]
        }
    },
    {
        "name": "search_drive",
        "description": "Search Mo's Google Drive files by name keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to match against file names"
                },
                "n": {
                    "type": "integer",
                    "description": "Maximum results to return. Default: 10"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "share_drive_file",
        "description": "Make a Drive file shareable ('anyone with link') and return the shareable URL. Accepts a file ID, full Drive URL, or a file name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id_or_name": {
                    "type": "string",
                    "description": "Drive file ID, full URL, or file name to search for"
                }
            },
            "required": ["file_id_or_name"]
        }
    },
    {
        "name": "upload_to_drive",
        "description": "Upload a local file from Mo's computer to Google Drive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "local_path": {
                    "type": "string",
                    "description": "Absolute path to the local file to upload"
                },
                "folder_id": {
                    "type": "string",
                    "description": "Optional Drive folder ID to upload into. If omitted, uploads to Drive root."
                }
            },
            "required": ["local_path"]
        }
    },
    {
        "name": "read_doc",
        "description": "Read the full text content of a Google Doc. Accepts a Doc ID or full URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Google Doc ID or full URL"
                }
            },
            "required": ["doc_id"]
        }
    },
    {
        "name": "append_to_doc",
        "description": "Append a paragraph of text to the end of a Google Doc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Google Doc ID or full URL"
                },
                "text": {
                    "type": "string",
                    "description": "Text to append as a new paragraph"
                }
            },
            "required": ["doc_id", "text"]
        }
    },
    {
        "name": "read_sheet",
        "description": "Read cells from a Google Sheets spreadsheet and return as a formatted table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID or full URL"
                },
                "range_name": {
                    "type": "string",
                    "description": "Sheet name or A1 range to read, e.g. 'Sheet1' or 'Sheet1!A1:D20'. Default: 'Sheet1'"
                }
            },
            "required": ["spreadsheet_id"]
        }
    },
    {
        "name": "append_sheet_row",
        "description": "Append a new row of values to a Google Sheet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {
                    "type": "string",
                    "description": "Spreadsheet ID or full URL"
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name, e.g. 'Sheet1' or 'Expenses'"
                },
                "values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of cell values for the new row, e.g. ['2026-06-16', 'Groceries', '350 EGP']"
                }
            },
            "required": ["spreadsheet_id", "sheet_name", "values"]
        }
    },
    {
        "name": "list_email_templates",
        "description": "List all saved email templates Mo has available.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_email_template",
        "description": "Retrieve a specific email template by name, including its subject and body with placeholders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name, e.g. 'professor_extension'"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "save_email_template",
        "description": "Save a new email template or update an existing one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string", "description": "Short identifier, e.g. 'professor_extension'"},
                "description": {"type": "string", "description": "One-line description of what the template is for"},
                "subject":     {"type": "string", "description": "Subject line, may contain {placeholders}"},
                "body":        {"type": "string", "description": "Email body, may contain {placeholders}"},
                "to_hint":     {"type": "string", "description": "Who this email is typically sent to"}
            },
            "required": ["name", "description", "subject", "body"]
        }
    },
    {
        "name": "delete_email_template",
        "description": "Delete a saved email template.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name to delete"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "youtube_search",
        "description": (
            "Search YouTube for a video by name and open the top result in Comet. "
            "Use when Mo says 'play X on youtube', 'find the video X', "
            "'search youtube for X', or names a song/clip he wants to watch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search YouTube for"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "youtube_latest",
        "description": (
            "Open the newest video from a YouTube channel in Comet. Use when Mo "
            "asks for the latest/newest video from a channel, or 'what did X post'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel handle (@name), display name, URL, or UC... channel ID",
                }
            },
            "required": ["channel"]
        }
    },
    {
        "name": "open_web_search",
        "description": (
            "Open a web search for a task in Comet so Mo can read the results "
            "himself. Use when he says 'search the web/internet for X', 'look X "
            "up', or 'google X' — i.e. he wants the browser, not a spoken answer. "
            "Use web_search instead when he wants YOU to read the web and answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to search for"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "open_comet",
        "description": (
            "Open the Comet browser, optionally at a URL. Use for 'open the "
            "browser', 'open comet', or 'open <site>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional URL to open"}
            }
        }
    },
    {
        "name": "get_youtube_transcript",
        "description": "Fetch the transcript/captions of a YouTube video so Claude can summarise, explain, or answer questions about it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":      {"type": "string", "description": "Full YouTube URL or bare video ID"},
                "language": {"type": "string", "description": "Preferred transcript language code. Default: 'en'."}
            },
            "required": ["url"]
        }
    },
    {
        "name": "convert_currency",
        "description": "Convert an amount from one currency to another using live exchange rates. Supports EGP, USD, EUR, SAR, AED, GBP, and more.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount":        {"type": "number", "description": "Amount to convert"},
                "from_currency": {"type": "string", "description": "Source currency code or name (e.g. 'EGP', 'USD', 'dollar')"},
                "to_currency":   {"type": "string", "description": "Target currency code or name (e.g. 'EUR', 'SAR')"}
            },
            "required": ["amount", "from_currency", "to_currency"]
        }
    },
    {
        "name": "get_exchange_rates",
        "description": "Show live exchange rates for a base currency against common currencies (USD, EUR, GBP, SAR, AED, EGP).",
        "input_schema": {
            "type": "object",
            "properties": {
                "base": {"type": "string", "description": "Base currency code. Default: EGP"}
            }
        }
    },
    {
        "name": "log_expense",
        "description": "Log a spending entry to the local expense tracker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount":      {"type": "number", "description": "Amount spent"},
                "currency":    {"type": "string", "description": "Currency code. Default: EGP"},
                "category":    {"type": "string", "description": "Category: food, transport, shopping, health, education, entertainment, bills, other"},
                "description": {"type": "string", "description": "What was bought / short note"}
            },
            "required": ["amount"]
        }
    },
    {
        "name": "get_expense_summary",
        "description": "Summarise Mo's logged expenses by category for a given period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "'today', 'week', or 'month'. Default: week"}
            }
        }
    },
    {
        "name": "list_recent_expenses",
        "description": "Show the most recent expense log entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of entries to show. Default: 10"}
            }
        }
    },
    {
        "name": "translate_text",
        "description": "Translate text to another language using MyMemory API. Use when Mo explicitly asks to translate to a specific language, or when the text is too long for in-context translation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text":            {"type": "string", "description": "Text to translate"},
                "target_language": {"type": "string", "description": "Target language name or code (e.g. 'French', 'fr', 'Spanish', 'es')"},
                "source_language": {"type": "string", "description": "Source language or 'auto'. Default: auto"}
            },
            "required": ["text", "target_language"]
        }
    },
    {
        "name": "wikipedia_lookup",
        "description": "Look up a person, concept, or topic on Wikipedia and return a concise summary. Faster than web_search for factual lookups.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string", "description": "Person or topic to look up"},
                "language": {"type": "string", "description": "Wikipedia edition to search. Default: 'en'."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_prayer_times",
        "description": "Get today's Islamic prayer times for Cairo (Fajr, Sunrise, Dhuhr, Asr, Maghrib, Isha) and show the next upcoming prayer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Optional date in YYYY-MM-DD format. Defaults to today."}
            }
        }
    },
    {
        "name": "enable_focus_mode",
        "description": "Block distracting websites (YouTube, Instagram, TikTok, Reddit, etc.) for a set number of hours by modifying the Windows hosts file. Requires admin rights.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "number", "description": "How many hours to block. Default: 2"},
                "sites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional custom list of domains to block. Uses defaults if omitted."
                }
            }
        }
    },
    {
        "name": "disable_focus_mode",
        "description": "Immediately unblock all sites and cancel the focus mode timer.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_clipboard_history",
        "description": "Show recent clipboard entries that El Fager has monitored. Useful for 'what did I copy earlier?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of recent entries to show. Default: 10"}
            }
        }
    },
    {
        "name": "analyze_screen",
        "description": "Capture the current screen and use Claude vision to answer a question about what's visible. Use when Mo asks to read something on screen, identify UI elements, describe what's showing, or answer questions about visible content. Unlike capture_screenshot (OCR only), this understands visual layout, graphs, icons, and images.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to analyze or find on screen. E.g. 'What error is shown?', 'Read the text in the top-right box', 'What app is open?', 'Describe what you see'"
                }
            },
            "required": ["question"]
        }
    },
    {
        "name": "ocr_screenshot",
        "description": "Extract all text from the current screen using OCR (Tesseract). Fast, free, no API call. Best for reading code, terminal output, documents, and text-heavy screens. Use analyze_screen when you need to understand visual layout, images, or graphs.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_system_volume",
        "description": "Set the Windows master volume level (0–100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume level 0–100"}
            },
            "required": ["level"]
        }
    },
    {
        "name": "get_system_volume",
        "description": "Return the current Windows master volume level and mute status.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "mute_system",
        "description": "Toggle the Windows system mute on or off.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "set_brightness",
        "description": "Set the screen brightness (0–100). Works on most laptops via WMI.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Brightness level 0–100"}
            },
            "required": ["level"]
        }
    },
    {
        "name": "get_battery_status",
        "description": "Return battery percentage, charging status, and estimated time remaining.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_process_info",
        "description": "List running processes with RAM and CPU usage. Filter by name or show top 15 by RAM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Optional process name filter (partial, case-insensitive), e.g. 'chrome', 'python'"}
            }
        }
    },
    {
        "name": "kill_process",
        "description": "Kill all running processes matching a name. Use with caution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Process name or partial match to kill, e.g. 'chrome', 'notepad'"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "send_telegram",
        "description": "Send a Telegram message to a saved contact via Mo's bot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_name": {"type": "string", "description": "Contact name (partial match OK)"},
                "message": {"type": "string", "description": "Message text to send"}
            },
            "required": ["contact_name", "message"]
        }
    },
    {
        "name": "get_telegram_messages",
        "description": "Fetch recent messages received by Mo's Telegram bot. Shows sender name, chat_id, and message text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of recent messages to fetch. Default: 10"}
            }
        }
    },
    {
        "name": "add_telegram_contact",
        "description": "Save a Telegram contact (name → chat_id) so Mo can message them by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contact display name"},
                "chat_id": {"type": "string", "description": "Telegram chat_id (found via get_telegram_messages)"}
            },
            "required": ["name", "chat_id"]
        }
    },
    {
        "name": "list_telegram_contacts",
        "description": "List all saved Telegram contacts with their chat_ids.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_news",
        "description": "Fetch top headlines from an RSS feed. Categories: world, tech, science, egypt, business, sports.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Feed category: world | tech | science | egypt | business | sports. Default: world"},
                "n": {"type": "integer", "description": "Number of headlines. Default: 5"}
            }
        }
    },
    {
        "name": "get_all_headlines",
        "description": "Fetch a few headlines from every news category — good for a morning briefing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Headlines per category. Default: 2"}
            }
        }
    },
    {
        "name": "search_news",
        "description": "Search across all news feeds for articles matching a keyword or phrase. Results are numbered — Mo can say 'read article N' to get the full story.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or phrase to search for"},
                "n": {"type": "integer", "description": "Max results to return. Default: 8"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_news_article",
        "description": "Fetch and return clean text from a news article. Pass an index (1, 2, 3...) from the last get_news/search_news call, or a full URL. Always summarise the returned text in 3-5 sentences — never read it verbatim.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_url": {"type": "string", "description": "Article number from last news list (e.g. '1') or a full article URL"}
            },
            "required": ["index_or_url"]
        }
    },
    {
        "name": "get_weather",
        "description": "Get current weather and today's high/low for a city. Default city: Cairo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name. Default: Cairo"}
            }
        }
    },
    {
        "name": "get_weather_forecast",
        "description": "Get a multi-day weather forecast for a city with UV index and rain probability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name. Default: Cairo"},
                "days": {"type": "integer", "description": "Number of forecast days (1-7). Default: 3"}
            }
        }
    },
    {
        "name": "get_hourly_weather",
        "description": "Get hour-by-hour weather for the next N hours. Good for 'what's the weather like at 3pm?' or planning around rain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name. Default: Cairo"},
                "hours": {"type": "integer", "description": "Number of hours ahead (1-24). Default: 12"}
            }
        }
    },
    {
        "name": "save_journal_entry",
        "description": "Save a timestamped note or journal entry to today's journal file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text":  {"type": "string", "description": "The note or journal entry text"},
                "title": {"type": "string", "description": "Optional short heading (e.g. 'Study notes', 'Random thought')"},
                "mood":  {"type": "string", "description": "Optional mood word, e.g. 'happy', 'stressed', 'focused', 'tired'"},
                "tags":  {"type": "array", "items": {"type": "string"}, "description": "Optional list of topic tags, e.g. ['study', 'uni', 'important']"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "read_journal",
        "description": "Read a journal file. Accepts natural dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Date: 'today', 'yesterday', 'Monday', '3 days ago', or YYYY-MM-DD. Default: today"}
            }
        }
    },
    {
        "name": "edit_journal_entry",
        "description": "Replace the body of a specific journal entry with new text (preserves timestamp).",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_number": {"type": "integer", "description": "1-indexed entry number within the day"},
                "new_text":     {"type": "string",  "description": "Replacement text for the entry body"},
                "date_str":     {"type": "string",  "description": "Date — defaults to today"}
            },
            "required": ["entry_number", "new_text"]
        }
    },
    {
        "name": "append_to_entry",
        "description": "Add more text to an existing journal entry without creating a new timestamp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_number":    {"type": "integer", "description": "1-indexed entry number"},
                "additional_text": {"type": "string",  "description": "Text to append"},
                "date_str":        {"type": "string",  "description": "Date — defaults to today"}
            },
            "required": ["entry_number", "additional_text"]
        }
    },
    {
        "name": "list_journal_entries",
        "description": "List recent days with journal entries, entry counts, and word totals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of recent days to show. Default: 7"}
            }
        }
    },
    {
        "name": "most_recent_entry",
        "description": "Return the single most recent journal entry across all days.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "search_journal",
        "description": "Search all journal entries for a keyword, phrase, mood, or #tag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword, phrase, mood, or tag to search for"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_by_tag",
        "description": "Search journals for a specific #tag (with or without # prefix).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "Tag to search for, e.g. 'study' or '#study'"}
            },
            "required": ["tag"]
        }
    },
    {
        "name": "list_tags",
        "description": "List all unique #tags used across all journal entries, sorted by frequency.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "mood_summary",
        "description": "Show mood entries from the past N days with a tally of moods.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back. Default: 7"}
            }
        }
    },
    {
        "name": "delete_journal_entry",
        "description": "Delete a specific entry from a day's journal (1 = first entry of that day).",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_number": {"type": "integer", "description": "1-indexed entry number within the day"},
                "date_str":     {"type": "string",  "description": "Date — defaults to today"}
            },
            "required": ["entry_number"]
        }
    },
    {
        "name": "get_journal_stats",
        "description": "Overall journal statistics: days, entries, words, date range, most active day.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "journal_streak",
        "description": "Count consecutive days Mo has journaled in a row.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "weekly_summary",
        "description": "Return all journal entries from the past 7 days for the AI to summarise.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weeks_ago": {"type": "integer", "description": "0 = this past week (default), 1 = the week before, etc."}
            }
        }
    },
    {
        "name": "read_journal_range",
        "description": "Read journal entries across a date range (max 14 days).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date — same formats as read_journal"},
                "end_date":   {"type": "string", "description": "End date — defaults to today"}
            },
            "required": ["start_date"]
        }
    },
    {
        "name": "run_python",
        "description": "Run Python code in an isolated subprocess and return the output. Use for calculations, data processing, or any Python snippet Mo asks to execute.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "run_powershell",
        "description": "Run a PowerShell command and return the output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "PowerShell command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "execute_file",
        "description": "Run a .py or .ps1 script file and return the output. Working directory is set to the file's parent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string",  "description": "Absolute path to the .py or .ps1 file"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "run_bash",
        "description": "Run a Bash/shell command via Git Bash (Windows) or /bin/bash. Use for pipelines, grep, find, file operations, or any Unix-style command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string",  "description": "Bash command to run"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "pip_install",
        "description": "Install a Python package via pip. Only safe package names (alphanumeric + hyphens/underscores) are accepted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package": {"type": "string",  "description": "Package name, e.g. 'numpy' or 'requests[security]'"},
                "upgrade": {"type": "boolean", "description": "Pass --upgrade flag (default false)"}
            },
            "required": ["package"]
        }
    },
    {
        "name": "list_packages",
        "description": "List installed Python packages. Optionally filter by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_str": {"type": "string", "description": "Filter packages by name substring (optional)"}
            },
            "required": []
        }
    },
    {
        "name": "get_python_info",
        "description": "Show Python version, executable path, and whether key packages (anthropic, PyQt6, numpy, etc.) are installed.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_with_stdin",
        "description": "Run Python code and feed it stdin text. Use this when the code calls input(). Pass each input on a new line.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code":        {"type": "string",  "description": "Python code to execute"},
                "stdin_input": {"type": "string",  "description": "Text to feed as stdin, lines separated by \\n"},
                "timeout":     {"type": "integer", "description": "Timeout in seconds (default 30)"}
            },
            "required": ["code", "stdin_input"]
        }
    },
    {
        "name": "run_with_args",
        "description": "Run a .py or .ps1 script file with command-line arguments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string",  "description": "Absolute path to the script"},
                "args":    {"type": "string",  "description": "Space-separated CLI arguments, e.g. '1 2 3' or '--verbose'"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "pip_uninstall",
        "description": "Uninstall a Python package via pip.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package": {"type": "string", "description": "Package name to uninstall"}
            },
            "required": ["package"]
        }
    },
    {
        "name": "pip_show",
        "description": "Show detailed info about an installed Python package: version, location, author, dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package": {"type": "string", "description": "Package name to inspect"}
            },
            "required": ["package"]
        }
    },
    {
        "name": "create_script",
        "description": "Save code as a named script in data/scripts/ for reuse across sessions. Language: python, powershell, bash, javascript.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":     {"type": "string", "description": "Script name (letters, numbers, hyphens, underscores)"},
                "code":     {"type": "string", "description": "The source code"},
                "language": {"type": "string", "description": "python | powershell | bash | javascript (default: python)"}
            },
            "required": ["name", "code"]
        }
    },
    {
        "name": "get_script",
        "description": "Retrieve and display the source code of a saved script.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Script name"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "list_scripts",
        "description": "List all saved scripts in data/scripts/ with name, language, line count, and last-modified time.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_script",
        "description": "Run a saved script by name. Optionally pass command-line arguments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string",  "description": "Script name"},
                "args":    {"type": "string",  "description": "Optional space-separated CLI args"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "delete_script",
        "description": "Delete a saved script from data/scripts/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Script name to delete"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "read_conversation",
        "description": "Read the logged conversation for a specific day. date_str accepts: today, yesterday, Monday, YYYY-MM-DD.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Date to read: 'today', 'yesterday', 'Monday', 'YYYY-MM-DD'"}
            },
            "required": []
        }
    },
    {
        "name": "search_conversations",
        "description": "Search all logged conversations for a query string. Returns matching turns with date+time context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for in conversation history"},
                "n": {"type": "integer", "description": "Max results (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "conversation_stats",
        "description": "Show conversation history statistics: total days, turns, avg turns/day, most-used tools.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "export_conversation",
        "description": "Export a day's conversation to a text file and open it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Date to export (default: today)"}
            },
            "required": []
        }
    },
    {
        "name": "spending_insights",
        "description": "Analyse spending patterns from expense log. Returns top categories, total, and daily average. period: week, month, year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "week, month, or year (default: month)"}
            },
            "required": []
        }
    },
    {
        "name": "journal_insights",
        "description": "Analyse journal entries: days journaled, words written, mood distribution, top tags, streak. period: week, month, year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "week, month, or year (default: month)"}
            },
            "required": []
        }
    },
    {
        "name": "productivity_insights",
        "description": "Analyse El Fager usage: conversation count and most-used tools. period: week, month, year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "week, month, or year (default: week)"}
            },
            "required": []
        }
    },
    {
        "name": "weekly_report",
        "description": "Combined weekly report: spending + journal + productivity in one spoken-friendly summary.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "mood_trend",
        "description": "Show mood trends from journal entries over the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Number of days to look back (default 14)"}
            },
            "required": []
        }
    },
    {
        "name": "top_tools",
        "description": "Show the most-used El Fager tools across all conversation history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of top tools to show (default 10)"}
            },
            "required": []
        }
    },
    {
        "name": "daily_activity",
        "description": "Full activity summary for a specific day: expenses, journal, conversations. Omit date for today.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format (default: today)"}
            },
            "required": []
        }
    },
    {
        "name": "streak_stats",
        "description": "All-time journaling streak stats: current streak, longest streak, total days, consistency %.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "log_income",
        "description": "Log a new income entry. Call when Mo says 'I got paid', 'received payment', 'client paid me', 'earned X', 'got X from Y'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount":      {"type": "number",  "description": "Amount received"},
                "currency":    {"type": "string",  "description": "Currency code, default EGP"},
                "category":    {"type": "string",  "description": "freelance, salary, consulting, sales, investment, gift, other"},
                "source":      {"type": "string",  "description": "Who paid (client name, company, etc.)"},
                "description": {"type": "string",  "description": "What was it for"},
                "invoice_id":  {"type": "string",  "description": "Related invoice ID if any (e.g. INV-0001)"}
            },
            "required": ["amount"]
        }
    },
    {
        "name": "get_income_summary",
        "description": "Income summary for today/week/month/year. Shows total, daily average, and category breakdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "today, week, month, year. Default: month"}
            },
            "required": []
        }
    },
    {
        "name": "list_recent_income",
        "description": "List the most recent income entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of entries to show. Default: 10"}
            },
            "required": []
        }
    },
    {
        "name": "create_invoice",
        "description": "Create a new invoice for a client. Status starts as 'draft'. due_date accepts YYYY-MM-DD, 'in 14 days', 'next Friday'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "client":      {"type": "string", "description": "Client name"},
                "amount":      {"type": "number", "description": "Invoice amount"},
                "description": {"type": "string", "description": "What the invoice is for"},
                "due_date":    {"type": "string", "description": "Due date: YYYY-MM-DD, 'in 14 days', 'next Friday'"},
                "currency":    {"type": "string", "description": "Currency, default EGP"},
                "notes":       {"type": "string", "description": "Optional notes"}
            },
            "required": ["client", "amount", "description", "due_date"]
        }
    },
    {
        "name": "send_invoice",
        "description": "Mark an invoice as sent (draft → sent). Use when Mo says he sent or delivered an invoice to a client.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID, e.g. INV-0001"}
            },
            "required": ["invoice_id"]
        }
    },
    {
        "name": "mark_invoice_paid",
        "description": "Mark an invoice as paid. Automatically logs the payment as income — do NOT also call log_income separately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID, e.g. INV-0001"},
                "paid_date":  {"type": "string", "description": "Date paid YYYY-MM-DD (default: today)"}
            },
            "required": ["invoice_id"]
        }
    },
    {
        "name": "list_invoices",
        "description": "List invoices. status_filter: all, draft, sent, paid, overdue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {"type": "string", "description": "all, draft, sent, paid, overdue. Default: all"}
            },
            "required": []
        }
    },
    {
        "name": "get_invoice",
        "description": "Get full details of a specific invoice by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID, e.g. INV-0001"}
            },
            "required": ["invoice_id"]
        }
    },
    {
        "name": "delete_invoice",
        "description": "Delete an invoice permanently.",
        "input_schema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice ID to delete"}
            },
            "required": ["invoice_id"]
        }
    },
    {
        "name": "set_budget",
        "description": "Set or update a spending budget for a category. E.g. food 3000 EGP per month.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Expense category (food, transport, etc.)"},
                "limit":    {"type": "number", "description": "Budget limit amount"},
                "period":   {"type": "string", "description": "week or month. Default: month"},
                "currency": {"type": "string", "description": "Currency, default EGP"}
            },
            "required": ["category", "limit"]
        }
    },
    {
        "name": "list_budgets",
        "description": "List all budgets with actual vs limit spending and a progress bar.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_budget",
        "description": "Delete a budget for a category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Budget category to delete"},
                "period":   {"type": "string", "description": "week or month. Default: month"}
            },
            "required": ["category"]
        }
    },
    {
        "name": "set_savings_goal",
        "description": "Create or update a savings goal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":     {"type": "string", "description": "Goal name, e.g. 'Emergency Fund'"},
                "target":   {"type": "number", "description": "Target amount to save"},
                "currency": {"type": "string", "description": "Currency, default EGP"},
                "deadline": {"type": "string", "description": "Target date YYYY-MM-DD (optional)"},
                "notes":    {"type": "string", "description": "Optional notes"}
            },
            "required": ["name", "target"]
        }
    },
    {
        "name": "update_savings_progress",
        "description": "Update how much Mo has saved toward a goal. Takes the TOTAL saved so far (absolute), not an increment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":         {"type": "string", "description": "Goal name"},
                "amount_saved": {"type": "number", "description": "Total amount saved so far (NOT a delta)"}
            },
            "required": ["name", "amount_saved"]
        }
    },
    {
        "name": "list_savings_goals",
        "description": "List all savings goals with progress bars.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "delete_savings_goal",
        "description": "Delete a savings goal by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Goal name to delete"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "cash_flow_summary",
        "description": "Cash flow: total income minus total expenses for a period. Shows net surplus or deficit and savings rate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "week, month, year. Default: month"}
            },
            "required": []
        }
    },
    {
        "name": "revenue_insights",
        "description": "Income breakdown by category and source for a period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "week, month, year. Default: month"}
            },
            "required": []
        }
    },
    {
        "name": "profit_loss_report",
        "description": "Full P&L report: revenue breakdown + expense breakdown + net cash flow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "week, month, year. Default: month"}
            },
            "required": []
        }
    },
    # ── Spotify Tools ──────────────────────────────────────────────────────────
    {
        "name": "play_music",
        "description": "Search and play a song, artist, album, or playlist on Spotify. Detects mood keywords (chill, focus, workout, sad, happy, sleep) and picks a matching playlist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Song name, artist, mood keyword, or playlist search term"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "pause_music",
        "description": "Pause or resume Spotify playback.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "next_track",
        "description": "Skip to the next track on Spotify.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "what_playing",
        "description": "Show what track is currently playing on Spotify with progress.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_volume",
        "description": "Set Spotify playback volume (0-100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume level 0-100"}
            },
            "required": ["level"]
        }
    },
    {
        "name": "spotify_status",
        "description": "Diagnose Spotify connection: account type, active devices, and current playback.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    # ── Image Generation Tools ──────────────────────────────────────────────────
    {
        "name": "generate_image",
        "description": "Generate an AI image with DALL-E 3. Expands the prompt, saves to data/generated_images/, and opens it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed image description"},
                "size": {"type": "string", "description": "1024x1024 (square) | 1792x1024 (landscape) | 1024x1792 (portrait)"},
                "quality": {"type": "string", "description": "standard (default) | hd"},
                "style": {"type": "string", "description": "vivid (dramatic) | natural (realistic)"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "generate_variation",
        "description": "Generate a variation of a previously generated image by modifying its prompt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string", "description": "1-based index or filename from list_generated_images"},
                "changes": {"type": "string", "description": "What to change, e.g. but at night, in anime style"},
                "size": {"type": "string"},
                "quality": {"type": "string"},
                "style": {"type": "string"}
            },
            "required": ["index_or_filename", "changes"]
        }
    },
    {
        "name": "list_generated_images",
        "description": "List recently generated images with their prompts and metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of images to show. Default: 10"}
            },
            "required": []
        }
    },
    {
        "name": "open_image",
        "description": "Open a previously generated image with the default viewer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string"}
            },
            "required": ["index_or_filename"]
        }
    },
    {
        "name": "delete_image",
        "description": "Delete a generated image and remove it from the index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string"}
            },
            "required": ["index_or_filename"]
        }
    },
    {
        "name": "clear_all_images",
        "description": "Delete ALL generated images and reset the index. Ask for confirmation first.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "search_images",
        "description": "Search previously generated images by keyword in their prompt text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_image_info",
        "description": "Get full metadata (prompt, size, quality, style, date) for a specific generated image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string"}
            },
            "required": ["index_or_filename"]
        }
    },
    {
        "name": "favorite_image",
        "description": "Toggle the favorite flag on a generated image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string"}
            },
            "required": ["index_or_filename"]
        }
    },
    {
        "name": "list_favorite_images",
        "description": "List all images marked as favorites.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_as_wallpaper",
        "description": "Set a generated image as the Windows desktop wallpaper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string"}
            },
            "required": ["index_or_filename"]
        }
    },
    {
        "name": "copy_image_path",
        "description": "Copy the absolute file path of a generated image to the clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index_or_filename": {"type": "string"}
            },
            "required": ["index_or_filename"]
        }
    },
    # ── Pomodoro ─────────────────────────────────────────────────────────────────
    {
        "name": "start_pomodoro",
        "description": "Start a countdown focus timer. Shows a Windows toast notification when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "Duration in minutes. Default: 25"},
                "label": {"type": "string", "description": "Session name. Default: Focus session"}
            },
            "required": []
        }
    },
    {
        "name": "stop_pomodoro",
        "description": "Cancel a running Pomodoro timer. Cancels all if label omitted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string"}
            },
            "required": []
        }
    },
    {
        "name": "list_pomodoros",
        "description": "List all active Pomodoro timers with time remaining.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    # ── Flashcards ────────────────────────────────────────────────────────────────
    {
        "name": "save_flashcards",
        "description": "Save Q&A flashcard pairs to a CSV file for Anki import.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of {front: question, back: answer} dicts"
                },
                "output_path": {"type": "string", "description": "Default: data/flashcards.csv"},
                "deck_name": {"type": "string", "description": "Anki deck name. Default: El Fager"}
            },
            "required": ["cards"]
        }
    },
    # ── Citation ──────────────────────────────────────────────────────────────────
    {
        "name": "resolve_doi",
        "description": "Fetch academic publication metadata from CrossRef for a DOI. Use to format APA/MLA/Chicago citations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI (10.XXXX/...) or doi.org URL"}
            },
            "required": ["doi"]
        }
    },
    # ── GitHub Tools ──────────────────────────────────────────────────────────────
    {
        "name": "list_repos",
        "description": "List GitHub repositories for Mo (or a specified user), sorted by last update.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "n": {"type": "integer", "description": "Default: 10"}
            },
            "required": []
        }
    },
    {
        "name": "list_issues",
        "description": "List open issues for a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repo name or owner/repo"},
                "n": {"type": "integer", "description": "Default: 10"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "list_prs",
        "description": "List open pull requests for a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "n": {"type": "integer", "description": "Default: 10"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "get_repo_info",
        "description": "Get details about a GitHub repository: stars, forks, language, last push, open issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"}
            },
            "required": ["repo"]
        }
    },
    # ── Scheduler Tools ───────────────────────────────────────────────────────────
    {
        "name": "add_schedule",
        "description": "Schedule a recurring or one-shot task. Accepts natural language: every day at 8am, every Monday, every 30 minutes, in 2 hours, every morning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "when": {"type": "string", "description": "Natural language or cron string"},
                "tool_name": {"type": "string", "description": "Tool to call (mutually exclusive with macro_name)"},
                "args": {"type": "object"},
                "macro_name": {"type": "string", "description": "Macro to run (mutually exclusive with tool_name)"},
                "description": {"type": "string"},
                "run_on_add": {"type": "boolean"}
            },
            "required": ["name", "when"]
        }
    },
    {
        "name": "list_schedules",
        "description": "List all scheduled tasks with status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "all | active | paused"}
            },
            "required": []
        }
    },
    {
        "name": "remove_schedule",
        "description": "Remove a scheduled task by name.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "pause_schedule",
        "description": "Pause a scheduled task (keeps it but stops firing).",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "resume_schedule",
        "description": "Resume a paused scheduled task.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "run_now",
        "description": "Immediately trigger a scheduled task once regardless of schedule.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "get_schedule",
        "description": "Get details about a specific scheduled task.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "schedule_history",
        "description": "Show recent task execution history across all schedules.",
        "input_schema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "description": "Default: 10"}},
            "required": []
        }
    },
    {
        "name": "reschedule",
        "description": "Change when a scheduled task fires.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "when": {"type": "string"}
            },
            "required": ["name", "when"]
        }
    },
    {
        "name": "pause_all_schedules",
        "description": "Pause all active scheduled tasks at once.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "resume_all_schedules",
        "description": "Resume all paused scheduled tasks at once.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "scheduler_status",
        "description": "Show whether the scheduler is running and how many jobs are active.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "job_history",
        "description": "Show execution history for a specific scheduled task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "n": {"type": "integer", "description": "Default: 10"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "schedule_stats",
        "description": "Show statistics across all schedules: success rate, total runs, most-run jobs.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "clear_history",
        "description": "Clear all schedule execution history logs.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    # ── Macro Tools ───────────────────────────────────────────────────────────────
    {
        "name": "create_macro",
        "description": "Create a named multi-step automation macro. Steps: {tool, args} | {type:speak, text} | {type:wait, seconds} | {type:notify, title, message}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object"}},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["name", "steps"]
        }
    },
    {
        "name": "run_macro",
        "description": "Run a named macro by executing its steps in sequence.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "list_macros",
        "description": "List all available macros with descriptions and tags.",
        "input_schema": {
            "type": "object",
            "properties": {"tag": {"type": "string", "description": "Filter by tag"}},
            "required": []
        }
    },
    {
        "name": "delete_macro",
        "description": "Delete a macro by name.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "get_macro",
        "description": "Show a macro full definition including all steps.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    },
    {
        "name": "clone_macro",
        "description": "Clone an existing macro under a new name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "new_name": {"type": "string"}
            },
            "required": ["source", "new_name"]
        }
    },
    {
        "name": "edit_macro",
        "description": "Edit a specific step in a macro by 0-based step index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "step_index": {"type": "integer"},
                "tool": {"type": "string"},
                "args": {"type": "object"},
                "type": {"type": "string"},
                "text": {"type": "string"},
                "seconds": {"type": "number"}
            },
            "required": ["name", "step_index"]
        }
    },
    {
        "name": "add_step",
        "description": "Append a new step to an existing macro.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tool": {"type": "string"},
                "args": {"type": "object"},
                "type": {"type": "string"},
                "text": {"type": "string"},
                "seconds": {"type": "number"},
                "title": {"type": "string"},
                "message": {"type": "string"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "remove_step",
        "description": "Remove a step from a macro by its 0-based index.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "step_index": {"type": "integer"}
            },
            "required": ["name", "step_index"]
        }
    },
    {
        "name": "macro_stats",
        "description": "Show usage statistics for all macros: run counts, last run times.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    # ── Code Tool Extras ──────────────────────────────────────────────────────────
    {
        "name": "run_node",
        "description": "Run JavaScript code with Node.js.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "integer", "description": "Default: 30"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "check_syntax",
        "description": "Instantly check Python code for syntax errors using AST parsing (no execution).",
        "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}
    },
    {
        "name": "benchmark",
        "description": "Benchmark Python code execution time across multiple runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "runs": {"type": "integer", "description": "Default: 5"},
                "timeout": {"type": "integer", "description": "Default: 60"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "format_python",
        "description": "Auto-format Python code using black or autopep8.",
        "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}
    },
    {
        "name": "run_in_background",
        "description": "Run a command in the background without blocking. Returns immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "label": {"type": "string"},
                "language": {"type": "string", "description": "python | powershell | bash"}
            },
            "required": ["command", "label"]
        }
    },
    {
        "name": "list_background",
        "description": "List all currently running background jobs and their status.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "kill_background",
        "description": "Kill a running background job by its label.",
        "input_schema": {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}
    },
    {
        "name": "open_in_editor",
        "description": "Open a file in VS Code or the default editor.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    },
    # ── Journal Extras ─────────────────────────────────────────────────────────────
    {
        "name": "random_memory",
        "description": "Return a random past journal entry as a memory.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "rename_entry_title",
        "description": "Rename the title of a journal entry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_number": {"type": "integer"},
                "new_title": {"type": "string"},
                "date_str": {"type": "string", "description": "today, yesterday, Monday, YYYY-MM-DD"}
            },
            "required": ["entry_number", "new_title"]
        }
    },
    {
        "name": "pin_entry",
        "description": "Pin/bookmark a journal entry for quick access.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_number": {"type": "integer"},
                "date_str": {"type": "string"}
            },
            "required": ["entry_number"]
        }
    },
    {
        "name": "list_pinned_entries",
        "description": "Show all pinned/bookmarked journal entries.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "export_journal",
        "description": "Export journal entries to a Markdown file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"}
            },
            "required": []
        }
    },
    {
        "name": "copy_journal_to_clipboard",
        "description": "Copy journal entries for a date to the clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {"type": "string", "description": "Default: today"}
            },
            "required": []
        }
    },
    # ── Mouse & Keyboard ─────────────────────────────────────────────────────
    {
        "name": "mouse_move",
        "description": "Move the mouse cursor to screen coordinates (x, y).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X screen coordinate"},
                "y": {"type": "integer", "description": "Y screen coordinate"},
                "duration": {"type": "number", "description": "Movement duration in seconds. Default 0.3."}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "mouse_click",
        "description": "Click the mouse at (x, y) or at current position. button: left (default), right, middle. clicks: 1 or 2.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (optional)"},
                "y": {"type": "integer", "description": "Y coordinate (optional)"},
                "button": {"type": "string", "description": "left, right, or middle. Default: left."},
                "clicks": {"type": "integer", "description": "Number of clicks. Default: 1."}
            }
        }
    },
    {
        "name": "mouse_double_click",
        "description": "Double-click at (x, y) or at current cursor position if coords omitted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate (optional)"},
                "y": {"type": "integer", "description": "Y coordinate (optional)"}
            }
        }
    },
    {
        "name": "mouse_drag",
        "description": "Hold left mouse button and drag from (x1, y1) to (x2, y2).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "Start X"},
                "y1": {"type": "integer", "description": "Start Y"},
                "x2": {"type": "integer", "description": "End X"},
                "y2": {"type": "integer", "description": "End Y"},
                "duration": {"type": "number", "description": "Drag duration in seconds. Default 0.5."}
            },
            "required": ["x1", "y1", "x2", "y2"]
        }
    },
    {
        "name": "mouse_scroll",
        "description": "Scroll the mouse wheel. amount: positive = scroll up, negative = scroll down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Scroll clicks: positive=up, negative=down"},
                "x": {"type": "integer", "description": "X coordinate to scroll at (optional)"},
                "y": {"type": "integer", "description": "Y coordinate to scroll at (optional)"}
            },
            "required": ["amount"]
        }
    },
    {
        "name": "type_text",
        "description": "Type text character by character using the keyboard. Types into the currently focused field or app.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
                "interval": {"type": "number", "description": "Delay between keystrokes in seconds. Default 0.03."}
            },
            "required": ["text"]
        }
    },
    {
        "name": "press_key",
        "description": "Press a single keyboard key. Examples: enter, esc, tab, backspace, delete, space, f1-f12, home, end, pageup, pagedown, up, down, left, right, ctrl, alt, shift, win.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name to press. E.g. enter, esc, f5, tab, delete"}
            },
            "required": ["key"]
        }
    },
    {
        "name": "hotkey",
        "description": "Press a keyboard shortcut (multiple keys simultaneously). E.g. ctrl+c to copy, ctrl+v to paste, alt+tab to switch apps, ctrl+z to undo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of keys to press together. E.g. ['ctrl', 'c'] for copy, ['alt', 'tab'] to switch apps."
                }
            },
            "required": ["keys"]
        }
    },
    {
        "name": "get_mouse_position",
        "description": "Return the current mouse cursor position (x, y) and screen resolution.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "screenshot_coords",
        "description": "Capture a small region of the screen around (x, y) for debugging. Saves to data/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Center X of region"},
                "y": {"type": "integer", "description": "Center Y of region"},
                "width": {"type": "integer", "description": "Region width in pixels. Default 100."},
                "height": {"type": "integer", "description": "Region height in pixels. Default 100."}
            },
            "required": ["x", "y"]
        }
    },
    # ── Window Management ─────────────────────────────────────────────────────
    {
        "name": "list_windows",
        "description": "List all open window titles. Optionally filter by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional keyword to filter window titles."}
            }
        }
    },
    {
        "name": "get_active_window",
        "description": "Return the title, position, and size of the currently focused window.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "read_window_text",
        "description": (
            "Read the text a window shows, exactly, through UI Automation — works "
            "while El Fager covers it, and only reads. Leave title empty for the "
            "window Mo was in before he called you ('what does it say', 'read me "
            "that'), or give part of a window's title. If it finds no text, "
            "fall back to a screenshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string",
                          "description": "Part of the window's title; empty for Mo's last window."}
            }
        }
    },
    {
        "name": "switch_to_window",
        "description": "Bring a window to the foreground by partial title match. E.g. 'Chrome', 'Word', 'Notepad'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title to match (case-insensitive)"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "minimize_window",
        "description": "Minimize a window. If title omitted, minimizes the active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title (optional — active window if omitted)"}
            }
        }
    },
    {
        "name": "maximize_window",
        "description": "Maximize a window. If title omitted, maximizes the active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title (optional — active window if omitted)"}
            }
        }
    },
    {
        "name": "restore_window",
        "description": "Restore a minimized or maximized window to its normal size.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title (optional — active window if omitted)"}
            }
        }
    },
    {
        "name": "close_window",
        "description": "Send a close signal to a window (app may prompt to save). Use kill_process for force-close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title to close"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "resize_window",
        "description": "Resize a window to specific width and height in pixels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title"},
                "width": {"type": "integer", "description": "Width in pixels"},
                "height": {"type": "integer", "description": "Height in pixels"}
            },
            "required": ["title", "width", "height"]
        }
    },
    {
        "name": "move_window",
        "description": "Move a window's top-left corner to screen coordinates (x, y).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title"},
                "x": {"type": "integer", "description": "X screen coordinate"},
                "y": {"type": "integer", "description": "Y screen coordinate"}
            },
            "required": ["title", "x", "y"]
        }
    },
    {
        "name": "snap_window",
        "description": "Snap a window to a screen position. position options: left, right, top-left, top-right, bottom-left, bottom-right, maximized, center.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title"},
                "position": {"type": "string", "description": "Where to snap: left, right, top-left, top-right, bottom-left, bottom-right, maximized, center"}
            },
            "required": ["title", "position"]
        }
    },
    # ── Browser Automation ────────────────────────────────────────────────────
    {
        "name": "browser_is_open",
        "description": "Check whether the browser is currently open and return the current URL. Call before browser_navigate or browser_click to verify state.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "browser_open",
        "description": "Launch a Playwright Chromium browser window. Optionally navigate to a URL immediately. headless=False (default) shows the browser so Mo can watch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to after opening (optional)"},
                "headless": {"type": "boolean", "description": "Run browser invisibly. Default false (visible)."}
            }
        }
    },
    {
        "name": "browser_navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_click",
        "description": "Click an element in the browser. Tries CSS selector, then XPath, then visible text match.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector_or_text": {"type": "string", "description": "CSS selector (e.g. '#submit'), XPath, or visible button/link text"}
            },
            "required": ["selector_or_text"]
        }
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field in the browser. Clears the field first by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the input field. E.g. '#email', 'input[name=q]'"},
                "text": {"type": "string", "description": "Text to type into the field"},
                "clear": {"type": "boolean", "description": "Clear field before typing. Default true."}
            },
            "required": ["selector", "text"]
        }
    },
    {
        "name": "browser_get_text",
        "description": "Get the visible text content of a page element (or the full page body if no selector given).",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector. Default: 'body' (full page text)."}
            }
        }
    },
    {
        "name": "browser_get_title",
        "description": "Return the current browser page title and URL.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current browser page and save it to data/browser_screenshots/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Optional custom save path. Defaults to data/browser_screenshots/."}
            }
        }
    },
    {
        "name": "browser_fill_form",
        "description": "Fill multiple form fields at once. fields is a dict of {CSS_selector: value}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "description": "Dict of CSS selector to value. E.g. {'#email': 'mo@example.com', '#name': 'Mo'}"
                }
            },
            "required": ["fields"]
        }
    },
    {
        "name": "browser_submit",
        "description": "Click a submit button or any element to submit a form.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of button/submit element. E.g. 'button[type=submit]', '#login-btn'"}
            },
            "required": ["selector"]
        }
    },
    {
        "name": "browser_wait",
        "description": "Wait for a number of seconds (for page load or animations to complete).",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Seconds to wait. Default 2."}
            }
        }
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the browser page. direction: down (default), up, top, bottom.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "description": "down, up, top, or bottom. Default: down."},
                "amount": {"type": "integer", "description": "Number of scroll steps. Default 3."}
            }
        }
    },
    {
        "name": "browser_close",
        "description": "Close the browser and release all resources.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "browser_back",
        "description": "Navigate the browser back to the previous page.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "browser_get_links",
        "description": "Get all links on the current browser page. Optionally filter by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional keyword to filter link text or href."}
            }
        }
    },
    {
        "name": "browser_select",
        "description": "Select an option from a dropdown (<select>) element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the <select> element"},
                "value": {"type": "string", "description": "Option value or visible text to select"}
            },
            "required": ["selector", "value"]
        }
    },
    # ── Business Calculator Tools ─────────────────────────────────────────────
    {
        "name": "startup_metrics",
        "description": "Calculate SaaS/startup KPIs: MRR, ARR, LTV, CAC, LTV/CAC ratio, and payback period with benchmarks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mrr": {"type": "number", "description": "Monthly Recurring Revenue"},
                "churn_rate": {"type": "number", "description": "Monthly churn % (e.g. 5 for 5%)"},
                "cac": {"type": "number", "description": "Cost to Acquire a Customer"},
                "avg_revenue_per_user": {"type": "number", "description": "ARPU if known; derived from MRR if omitted"}
            },
            "required": ["mrr", "churn_rate", "cac"]
        }
    },
    {
        "name": "burn_runway",
        "description": "Calculate startup burn rate runway: months of cash remaining and zero-cash date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "monthly_burn": {"type": "number", "description": "Monthly cash burn"},
                "cash": {"type": "number", "description": "Cash on hand"}
            },
            "required": ["monthly_burn", "cash"]
        }
    },
    {
        "name": "break_even",
        "description": "Break-even analysis: units and revenue needed to cover fixed costs given variable cost and price per unit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fixed_costs": {"type": "number", "description": "Total fixed costs"},
                "variable_cost_per_unit": {"type": "number", "description": "Variable cost per unit"},
                "price_per_unit": {"type": "number", "description": "Selling price per unit"}
            },
            "required": ["fixed_costs", "variable_cost_per_unit", "price_per_unit"]
        }
    },
    {
        "name": "margin_analysis",
        "description": "Full P&L margin waterfall: gross profit, EBIT, net income as % of revenue with benchmarks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "revenue": {"type": "number"},
                "cogs": {"type": "number", "description": "Cost of Goods Sold"},
                "operating_expenses": {"type": "number", "description": "OpEx excluding COGS"},
                "tax_rate": {"type": "number", "description": "Tax rate %. Default: 22.5"}
            },
            "required": ["revenue", "cogs", "operating_expenses"]
        }
    },
    {
        "name": "roi_calc",
        "description": "Calculate ROI, annualised ROI, and payback period given investment and gross return.",
        "input_schema": {
            "type": "object",
            "properties": {
                "investment": {"type": "number"},
                "gross_return": {"type": "number"},
                "years": {"type": "number", "description": "Investment horizon in years. Default: 1"}
            },
            "required": ["investment", "gross_return"]
        }
    },
    {
        "name": "dcf_value",
        "description": "Discounted Cash Flow (DCF) intrinsic business valuation given projected cash flows, WACC, and terminal growth rate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cash_flows": {"type": "array", "items": {"type": "number"}, "description": "List of projected annual cash flows"},
                "discount_rate": {"type": "number", "description": "WACC or required return % (e.g. 10)"},
                "terminal_growth": {"type": "number", "description": "Perpetual growth rate %. Default: 2"}
            },
            "required": ["cash_flows", "discount_rate"]
        }
    },
    {
        "name": "valuation_multiples",
        "description": "Business valuation using revenue, EBITDA, or P/E multiples with Egypt and US benchmarks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "revenue": {"type": "number"},
                "ebitda": {"type": "number"},
                "net_income": {"type": "number"},
                "rev_multiple": {"type": "number"},
                "ebitda_multiple": {"type": "number"},
                "pe_multiple": {"type": "number"}
            },
            "required": []
        }
    },
    {
        "name": "loan_payment",
        "description": "Calculate monthly loan EMI, total paid, and total interest for a given principal, annual rate, and term.",
        "input_schema": {
            "type": "object",
            "properties": {
                "principal": {"type": "number"},
                "annual_rate": {"type": "number", "description": "Annual interest rate %"},
                "months": {"type": "integer", "description": "Loan term in months"}
            },
            "required": ["principal", "annual_rate", "months"]
        }
    },
    {
        "name": "compound_growth",
        "description": "Future value of an investment with compound interest and optional monthly contributions. Includes Rule of 72.",
        "input_schema": {
            "type": "object",
            "properties": {
                "initial": {"type": "number", "description": "Initial investment"},
                "annual_rate": {"type": "number", "description": "Annual growth/interest rate %"},
                "years": {"type": "number"},
                "monthly_addition": {"type": "number", "description": "Optional monthly contribution"}
            },
            "required": ["initial", "annual_rate", "years"]
        }
    },
    {
        "name": "cagr_calc",
        "description": "Calculate Compound Annual Growth Rate (CAGR) between a start and end value over a number of years.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_value": {"type": "number"},
                "end_value": {"type": "number"},
                "years": {"type": "number"}
            },
            "required": ["start_value", "end_value", "years"]
        }
    },
    # ── System Health Tools ───────────────────────────────────────────────────
    {
        "name": "system_health",
        "description": "Full system status report: CPU %, RAM %, disk space, uptime, and top CPU-consuming processes. Call this when Mo asks 'how's my laptop doing?', 'check my PC', 'is my computer ok?'.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_disk_space",
        "description": "Free/used/total disk space for a drive. Default is C:\\.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Drive path, e.g. 'C:\\' or 'D:\\'. Default C:\\."}
            }
        }
    },
    {
        "name": "get_cpu_usage",
        "description": "Current CPU usage percentage averaged over an interval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "interval": {"type": "number", "description": "Sampling interval in seconds. Default 1."}
            }
        }
    },
    {
        "name": "get_ram_usage",
        "description": "RAM total, used, available, and usage percentage.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_system_uptime",
        "description": "How long the system has been running since last boot.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_top_processes",
        "description": "Top N processes by CPU usage, with RAM usage shown. Default N=5.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of processes to return. Default 5."}
            }
        }
    },
    # ── Network Tools ─────────────────────────────────────────────────────────
    {
        "name": "check_internet",
        "description": "Fast internet connectivity check — DNS test to 8.8.8.8. Returns 'connected' or 'NOT connected'. Use before making API calls when connection is uncertain.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_network_status",
        "description": "Full network info: local IP, WiFi SSID, signal strength, and default gateway.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "ping",
        "description": "Ping a host and return average latency and packet loss. Good for diagnosing connection issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname or IP to ping, e.g. 'google.com' or '8.8.8.8'"},
                "count": {"type": "integer", "description": "Number of ping packets. Default 4."}
            },
            "required": ["host"]
        }
    },
    {
        "name": "internet_speed",
        "description": "Run a speed test and return download/upload Mbps and ping. Takes ~10 seconds. Warn Mo before calling.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_local_ip",
        "description": "Local IPv4 address of Mo's machine on the current network.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_public_ip",
        "description": "External/public IPv4 address as seen from the internet.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    # ── PDF Tools ────────────────────────────────────────────────────────────
    {
        "name": "create_pdf",
        "description": "Create a PDF file from plain text. Good for saving journal entries, reports, or notes as PDFs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text content to put in the PDF"},
                "output_path": {"type": "string", "description": "Where to save the PDF, e.g. 'data/report.pdf'"},
                "title": {"type": "string", "description": "Optional document title shown at the top"}
            },
            "required": ["text", "output_path"]
        }
    },
    {
        "name": "merge_pdfs",
        "description": "Combine multiple PDF files into a single PDF.",
        "input_schema": {
            "type": "object",
            "properties": {
                "input_paths": {"type": "array", "items": {"type": "string"}, "description": "List of PDF file paths to merge in order"},
                "output_path": {"type": "string", "description": "Output path for the merged PDF"}
            },
            "required": ["input_paths", "output_path"]
        }
    },
    {
        "name": "split_pdf",
        "description": "Extract specific pages from a PDF into a new file. pages format: '1-3,5,7-9' (1-indexed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "input_path": {"type": "string"},
                "pages": {"type": "string", "description": "Page range, e.g. '1-3' or '1,3,5-7'"},
                "output_path": {"type": "string"}
            },
            "required": ["input_path", "pages", "output_path"]
        }
    },
    {
        "name": "compress_pdf",
        "description": "Reduce a PDF's file size by compressing content streams. Creates a new file (or _compressed variant if no output path given).",
        "input_schema": {
            "type": "object",
            "properties": {
                "input_path": {"type": "string"},
                "output_path": {"type": "string", "description": "Optional output path. If omitted, creates input_compressed.pdf"}
            },
            "required": ["input_path"]
        }
    },
    {
        "name": "pdf_info",
        "description": "Get page count, file size, and metadata (title, author, creation date) for a PDF.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "pdf_to_text",
        "description": "Extract all text from a PDF or specific pages. Returns the raw text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pages": {"type": "string", "description": "Optional page range, e.g. '1-3,5'. If omitted, extracts all pages."}
            },
            "required": ["path"]
        }
    },
    # ── Screen Recording Tools ────────────────────────────────────────────────
    {
        "name": "start_recording",
        "description": "Start recording the screen as an MP4 video in the background. Default FPS is 15. Saves to data/recordings/ if no path given.",
        "input_schema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "Optional output file path. Auto-named with timestamp if omitted."},
                "fps": {"type": "integer", "description": "Frames per second. Default 15. Lower = smaller file."}
            }
        }
    },
    {
        "name": "stop_recording",
        "description": "Stop the active screen recording, finalize the MP4 file, and return the saved file path.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "recording_status",
        "description": "Check if screen recording is currently active, and show elapsed time and frame count.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "take_snapshot",
        "description": "Save a single screenshot as PNG to data/recordings/. Faster and simpler than analyze_screen — just saves the image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "Optional output path. Auto-named with timestamp if omitted."}
            }
        }
    },
    # ── Printer Tools ─────────────────────────────────────────────────────────
    {
        "name": "list_printers",
        "description": "List all installed printers and show which one is the default.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "print_file",
        "description": "Send a file to a printer. Uses the default printer if printer name is omitted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file to print"},
                "printer": {"type": "string", "description": "Optional printer name. Uses default if omitted."}
            },
            "required": ["path"]
        }
    },
    {
        "name": "print_text",
        "description": "Print plain text by converting it to a PDF first and sending to the printer. Use for quick text printing without needing a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "title": {"type": "string", "description": "Document title. Default: 'El Fager'"},
                "printer": {"type": "string", "description": "Optional printer name. Uses default if omitted."}
            },
            "required": ["text"]
        }
    },
    {
        "name": "get_default_printer",
        "description": "Return the name of the current default printer.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "set_default_printer",
        "description": "Set a printer as the Windows default printer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Printer name (partial match supported)"}
            },
            "required": ["name"]
        }
    },
    # ── File Operations ───────────────────────────────────────────────────────
    {
        "name": "create_folder",
        "description": "Create a new folder (and all parent folders). Safe to call if it already exists.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Full path to create"}},
            "required": ["path"]
        }
    },
    {
        "name": "rename_file",
        "description": "Rename a file or folder in place. new_name is just the new filename, not a full path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Current full path of the file or folder"},
                "new_name": {"type": "string", "description": "New filename only, e.g. 'report_v2.pdf'"}
            },
            "required": ["path", "new_name"]
        }
    },
    {
        "name": "copy_file",
        "description": "Copy a file to a new path. If destination is a folder, copies inside it. Also works for copying whole folders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string", "description": "Destination file path or folder path"}
            },
            "required": ["src", "dst"]
        }
    },
    {
        "name": "move_file",
        "description": "Move (or rename to a different path) a file or folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string"}
            },
            "required": ["src", "dst"]
        }
    },
    {
        "name": "delete_file",
        "description": "Permanently delete a file. For folders, deletes recursively. ALWAYS confirm with Mo before calling — cannot be undone.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        }
    },
    {
        "name": "list_folder",
        "description": "List contents of a folder with file types, sizes, and modification dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "show_hidden": {"type": "boolean", "description": "Include hidden files. Default false."}
            },
            "required": ["path"]
        }
    },
    # ── Archive Tools ─────────────────────────────────────────────────────────
    {
        "name": "zip_files",
        "description": "Create a .zip archive from a list of files and/or folders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "List of file/folder paths to include"},
                "output_path": {"type": "string", "description": "Output .zip path, e.g. 'data/archive.zip'"}
            },
            "required": ["paths", "output_path"]
        }
    },
    {
        "name": "unzip_archive",
        "description": "Extract a .zip archive to a folder. If no output_dir given, extracts to a folder named after the zip.",
        "input_schema": {
            "type": "object",
            "properties": {
                "zip_path": {"type": "string"},
                "output_dir": {"type": "string", "description": "Optional destination folder."}
            },
            "required": ["zip_path"]
        }
    },
    {
        "name": "list_archive",
        "description": "List the contents of a .zip file without extracting it.",
        "input_schema": {
            "type": "object",
            "properties": {"zip_path": {"type": "string"}},
            "required": ["zip_path"]
        }
    },
    {
        "name": "add_to_archive",
        "description": "Add files or folders to an existing .zip, or create the zip if it doesn't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "zip_path": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["zip_path", "paths"]
        }
    },
    # ── Image Editing ─────────────────────────────────────────────────────────
    {
        "name": "resize_image",
        "description": "Resize an existing image file. If height is omitted, aspect ratio is preserved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer", "description": "Optional — auto-calculated if omitted."},
                "output": {"type": "string", "description": "Output path. Auto-named if omitted."}
            },
            "required": ["path", "width"]
        }
    },
    {
        "name": "crop_image",
        "description": "Crop an image to a rectangular region defined by pixel coordinates (left, top, right, bottom).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "left": {"type": "integer"}, "top": {"type": "integer"},
                "right": {"type": "integer"}, "bottom": {"type": "integer"},
                "output": {"type": "string"}
            },
            "required": ["path", "left", "top", "right", "bottom"]
        }
    },
    {
        "name": "convert_image",
        "description": "Convert an image between formats (PNG, JPEG, WebP, BMP, TIFF). Target format is inferred from the output file extension.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "output_path": {"type": "string", "description": "Destination path with new extension, e.g. 'photo.jpg'"}
            },
            "required": ["path", "output_path"]
        }
    },
    {
        "name": "compress_image",
        "description": "Reduce a JPEG or WebP image file size by lowering quality. quality 1-95, default 75.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "quality": {"type": "integer", "description": "Quality 1-95. Default 75."},
                "output": {"type": "string"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "rotate_image",
        "description": "Rotate an image file. Positive degrees = counter-clockwise. Common: 90, 180, 270.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "degrees": {"type": "integer"},
                "output": {"type": "string"}
            },
            "required": ["path", "degrees"]
        }
    },
    # ── Unit Conversion ───────────────────────────────────────────────────────
    {
        "name": "convert_units",
        "description": "Convert between units: temperature (C/F/K), length (m/km/mi/ft), weight (kg/lb/oz), volume (l/gal/ml), speed (km/h/mph), area, data size (GB/MB/GiB), time, pressure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string", "description": "e.g. 'km', 'F', 'lb', 'GB'"},
                "to_unit": {"type": "string", "description": "e.g. 'miles', 'C', 'kg', 'MB'"}
            },
            "required": ["value", "from_unit", "to_unit"]
        }
    },
    {
        "name": "list_unit_categories",
        "description": "Show all unit categories and unit names supported by convert_units.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    # ── Local Git ─────────────────────────────────────────────────────────────
    {
        "name": "git_status",
        "description": "Show local git repository status — modified, staged, and untracked files. repo_path defaults to current directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to the git repo. Default '.'"}
            }
        }
    },
    {
        "name": "git_log",
        "description": "Show recent commit history for a local git repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "n": {"type": "integer", "description": "Number of commits to show. Default 10."}
            }
        }
    },
    {
        "name": "git_diff",
        "description": "Show file changes in a local repo — unstaged by default, or staged if staged=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "staged": {"type": "boolean", "description": "Show staged (--cached) diff. Default false."}
            }
        }
    },
    {
        "name": "git_add",
        "description": "Stage files for git commit. Use paths=['.'] to stage all changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Files to stage. Use ['.'] for all."},
                "repo_path": {"type": "string"}
            },
            "required": ["paths"]
        }
    },
    {
        "name": "git_commit",
        "description": "Commit staged changes to a local git repository with a message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "repo_path": {"type": "string"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "git_push",
        "description": "Push local commits to a remote git repository. ALWAYS confirm with Mo before pushing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "remote": {"type": "string", "description": "Default 'origin'."},
                "branch": {"type": "string", "description": "Branch name. Defaults to current branch."}
            }
        }
    },
    {
        "name": "git_pull",
        "description": "Pull latest changes from a remote repository into the local branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string"},
                "remote": {"type": "string", "description": "Default 'origin'."}
            }
        }
    },
    # ── Developer Utilities ───────────────────────────────────────────────────
    {
        "name": "hash_text",
        "description": "Hash a string using md5, sha1, sha256, or sha512.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "algorithm": {"type": "string", "description": "md5, sha1, sha256, or sha512. Default sha256."}
            },
            "required": ["text"]
        }
    },
    {
        "name": "encode_base64",
        "description": "Base64 encode a string.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"]
        }
    },
    {
        "name": "decode_base64",
        "description": "Decode a Base64 encoded string back to plain text.",
        "input_schema": {
            "type": "object",
            "properties": {"encoded": {"type": "string"}},
            "required": ["encoded"]
        }
    },
    {
        "name": "url_encode",
        "description": "URL-encode a string — convert special characters to percent-encoding.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"]
        }
    },
    {
        "name": "url_decode",
        "description": "Decode a percent-encoded URL string back to readable text.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"]
        }
    },
    {
        "name": "generate_password",
        "description": "Generate a cryptographically secure random password.",
        "input_schema": {
            "type": "object",
            "properties": {
                "length": {"type": "integer", "description": "Length 4-128. Default 16."},
                "include_symbols": {"type": "boolean", "description": "Include symbols. Default true."}
            }
        }
    },
    {
        "name": "generate_uuid",
        "description": "Generate a random UUID4 — useful for database IDs or unique identifiers in code.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "generate_qr",
        "description": "Generate a QR code PNG image from any text or URL. Saved to data/ folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text or URL to encode"},
                "output_path": {"type": "string", "description": "Output PNG path. Auto-named if omitted."}
            },
            "required": ["text"]
        }
    },
    {
        "name": "add_autonomous_task",
        "description": "Queue a task for El Fager to execute autonomously in the background. Use when Mo delegates work: 'research X tonight', 'check NVDA RSI every day', 'do X for me later'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Full natural-language description of what El Fager should do. Be specific — El Fager will call brain.chat(description) to execute it."
                },
                "delay_hours": {
                    "type": "number",
                    "description": "Hours from now to wait before running. 0 = run immediately on next check (within 60s). 8 = tonight if queued in morning."
                },
                "recurring_hours": {
                    "type": "number",
                    "description": "If > 0, re-queue automatically every N hours after each completion. Use for daily/weekly monitoring tasks."
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "list_autonomous_tasks",
        "description": "Show all queued, running, done, and failed autonomous tasks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "delete_autonomous_task",
        "description": "Remove an autonomous task from the queue by its id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The 8-char task id shown in list_autonomous_tasks."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "ask_mo",
        "description": (
            "Ask Mo a question on WhatsApp when you can't go on without him and he isn't "
            "talking to you right now -- in a background task or a mission step. His reply "
            "comes back to you as a new background task with his answer. One clear question; "
            "never for things you can decide yourself, and never while he's talking to you "
            "(then just ask him)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"]
        }
    },
    {
        "name": "send_notification",
        "description": "Send a message to Mo's WhatsApp via CallMeBot. Use when Mo asks to be pinged, notified, or sent a WhatsApp from El Fager.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to Mo's WhatsApp."
                },
                "channel": {
                    "type": "string",
                    "description": "Notification channel. Default: 'whatsapp'. Use 'all' to send to all configured channels.",
                    "enum": ["whatsapp", "all"],
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "notification_status",
        "description": "Check if phone notifications are configured and working. Returns setup instructions if not configured.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "screen_agent",
        "description": (
            "Multi-step desktop control agent -- sees the screen and performs a sequence of "
            "clicks, typing, and keyboard shortcuts to complete a task (up to 10 internal steps). "
            "Use for: clicking buttons/links, dragging files, scrolling, multi-step UI automation "
            "('open and then...', 'automate the...', 'control the app'). Do NOT use for a single "
            "one-shot description of the screen -- use analyze_screen for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The user's desktop-control request, verbatim or lightly cleaned up."
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "browser_agent",
        "description": (
            "Multi-step browser automation agent -- navigates websites, fills forms, logs in, and "
            "completes multi-step web tasks. Use for: 'book a table/flight', 'log into', "
            "'fill out the form', 'search on amazon/google', or any task naming a specific website "
            "or '.com/.org/.net'. Do NOT use for one-off single actions when a simpler browser_* "
            "instant tool suffices."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The user's web-automation request, verbatim or lightly cleaned up."
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "research_agent",
        "description": (
            "Deep multi-source web research agent -- searches, reads multiple pages, and "
            "synthesizes a single coherent answer. Use for: 'research everything about X', "
            "'tell me everything about X', 'investigate X', 'comprehensive analysis of X', "
            "'compare and contrast X and Y', 'summarize the news about X'. Do NOT use for quick "
            "factual lookups -- use wikipedia_lookup or web_search for those. It returns a "
            "finished answer with sources: relay it to Mo, and don't repeat the research with "
            "more searches unless it says it found nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The research question or topic, verbatim or lightly cleaned up."
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "file_agent",
        "description": (
            "Document intelligence agent -- reads and answers questions about PDFs, Word docs, "
            "spreadsheets, and images. Use for: 'summarize this pdf/document/contract/invoice/"
            "thesis/report', 'what does this file say', 'extract from this', 'what were the "
            "payment terms'. Pass the file reference and the question together in the task string."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The file reference and question together, e.g. 'summarize my_contract.pdf'."
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "health_agent",
        "description": (
            "Nutrition and gym tracking agent -- logs meals, calculates macros/TDEE, generates "
            "workout programs and recipes. Use for: 'I just ate X', 'log my meal', 'calories "
            "today', 'my macros', 'recipe for X', 'chest day', 'finished my workout', 'generate a "
            "training program', 'what should I do today at the gym'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The user's nutrition or workout request, verbatim or lightly cleaned up."
                }
            },
            "required": ["task"]
        }
    },
    {
        "name": "job_search_agent",
        "description": (
            "Job search agent -- finds internships and entry-level jobs in Egypt on Wuzzuf "
            "and LinkedIn and returns a ranked list with links. Mo dropped Bayt and Forasna. "
            "Use for: 'find me jobs', 'any new internships?', 'data analyst jobs in Cairo', "
            "'what's on Wuzzuf'. query is the role Mo named ('data analyst internship'); omit it when he "
            "names none and it searches all his target roles. It lists only jobs not shown "
            "before; show_all=true lists them again. It only reads postings and never "
            "applies: relay the list to Mo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The role to search, e.g. 'data analyst internship'. Omit when Mo "
                        "names no role: it then searches all his target roles."
                    )
                },
                "show_all": {
                    "type": "boolean",
                    "description": "True to include jobs already shown ('show them again')."
                }
            }
        }
    },
    {
        "name": "prepare_applications",
        "description": (
            "Start preparing today's batch of job applications in the background: search "
            "the job boards and the Big 4 career sites, score each job against Mo's CV, and "
            "draft a tailored application for each good fit. Nothing is sent -- the batch "
            "waits for his review. Use for 'prepare my applications', 'run the job hunt'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "review_applications",
        "description": (
            "List the drafted applications waiting for Mo's approval (id, job, company, "
            "score, channel) and the link to the review page. Use for 'what applications "
            "are ready', 'show me the batch'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "approve_applications",
        "description": (
            "Approve the waiting batch of job applications and start sending them. With no "
            "input it approves every ready one. skip: ids to leave out. only: approve just "
            "these ids and skip the rest. Ids come from review_applications. Only call this "
            "when Mo has clearly said to approve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skip": {"type": "array", "items": {"type": "string"}},
                "only": {"type": "array", "items": {"type": "string"}}
            }
        }
    },
    {
        "name": "application_status",
        "description": (
            "Where Mo's job applications stand: practice or live mode, counts by status "
            "(ready, applied, interview, rejected...), sent this week, follow-ups due, and "
            "form answers still missing. Use for 'how are my applications going'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "check_application_replies",
        "description": (
            "Read Mo's inbox for replies from companies he applied to, and record "
            "interviews and rejections. Use for 'did anyone reply', 'any interviews'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "import_cv",
        "description": (
            "Read Mo's CV (a PDF or Word file path) into the profile the job applications "
            "are scored and written from, and the file they attach. Use when he says "
            "'import my CV from ...' or 'my new CV is at ...'. The main CV is for AI and "
            "data engineering roles. erp=true for his ERP CV, used for ERP roles (SAP, Odoo, "
            "Dynamics...); analyst=true for his data analyst CV, used for data analyst/BI "
            "roles and graduate programmes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "erp": {"type": "boolean"},
                           "analyst": {"type": "boolean"}},
            "required": ["path"]
        }
    },
    {
        "name": "set_application_answer",
        "description": (
            "Save Mo's answer to a question job application forms ask, used by every form "
            "from then on. question is one of phone, email, linkedin_url, military_status, "
            "graduation_year, gpa, expected_salary, availability, english_level, "
            "willing_to_relocate -- or, for anything else a form asked, the form's question "
            "word for word. An application that stopped on that question is retried. Use "
            "when he says e.g. 'my military status is exempted'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "answer": {"type": "string"}
            },
            "required": ["question", "answer"]
        }
    },
    {
        "name": "retry_applications",
        "description": (
            "Send again the applications that stopped waiting for Mo -- a site needed him "
            "to sign in or make an account, or a form asked something. Use when he says "
            "'I signed in, try again' or 'retry my applications'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "application_settings",
        "description": (
            "Show or change the job-application settings; with no input it shows them. "
            "live=true sends approved applications for real (needs his CV imported), "
            "live=false is practice mode. daily_target, min_score (0-100), linkedin_daily_cap and "
            "referrals_per_day are numbers. Change only what "
            "Mo asked to change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "live": {"type": "boolean"},
                "daily_target": {"type": "integer"},
                "min_score": {"type": "integer"},
                "linkedin_daily_cap": {"type": "integer"},
                "referrals_per_day": {"type": "integer"}
            }
        }
    },
    {
        "name": "graduate_programmes",
        "description": (
            "The graduate programmes at the Big 4 and top companies in Cairo: open, upcoming "
            "or closed, deadlines, and whether fresh graduates can apply. check=true reads "
            "every programme page again first (slower). Use for 'which graduate programmes "
            "are open', 'any deadlines coming up'."
        ),
        "input_schema": {"type": "object", "properties": {"check": {"type": "boolean"}}}
    },
    {
        "name": "find_referrals",
        "description": (
            "Find people at a target company (alumni of Mo's university first) who could "
            "refer him, and draft a LinkedIn connection note and a referral request for each. "
            "Mo sends them himself. company is optional; without it, companies with his "
            "applications in play come first. Use for 'who can refer me at PwC'."
        ),
        "input_schema": {"type": "object", "properties": {"company": {"type": "string"}}}
    },
    {
        "name": "skill_gaps",
        "description": (
            "What the jobs El Fager scored keep asking for that Mo's CV doesn't show, most "
            "asked first, with how many jobs asked. Use for 'what should I learn', 'what "
            "skills am I missing', 'why am I not a fit'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "evaluate_job",
        "description": (
            "Judge one job posting Mo found himself, from its link: whether it's still "
            "open, its fit score against his CV, what it asks that he lacks, and whether "
            "it's worth applying. A fit is saved and gets its letter in the next job hunt. "
            "Use for 'is this job worth it: <link>', 'check this posting', 'check the job "
            "I copied'. Leave url out when Mo gives none: the link he copied is used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}}
        }
    },
    {
        "name": "mark_followed_up",
        "description": (
            "Record that Mo followed up on a sent application (email to HR, or a message "
            "to the recruiter or his contact there). app_id comes from application_status, "
            "which lists the follow-ups due: a week after sending, then once more a week "
            "later. Use for 'I followed up with Valeo'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"app_id": {"type": "string"}},
            "required": ["app_id"]
        }
    },
    {
        "name": "import_linkedin_connections",
        "description": (
            "Import Mo's LinkedIn connections export (Connections.csv: LinkedIn Settings > "
            "Data privacy > Get a copy of your data > Connections). Keeps only the people "
            "at target companies; find_referrals then asks them first, before strangers. "
            "Use for 'import my LinkedIn connections'. Leave path out unless Mo names a "
            "file: the newest export in Downloads (the CSV or LinkedIn's zip) is used."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}}
        }
    },
    {
        "name": "referral_list",
        "description": (
            "The referral notes waiting for Mo to send (id, person, company, profile link), "
            "and how many were sent or led to a referral."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "mark_referral",
        "description": (
            "Record what happened with a referral contact: status is sent, replied, referred "
            "or skipped. referral_id comes from referral_list. Use when Mo says 'I sent the "
            "note to Ahmed' or 'Sara referred me'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "referral_id": {"type": "string"},
                "status": {"type": "string", "enum": ["sent", "replied", "referred", "skipped"]}
            },
            "required": ["referral_id", "status"]
        }
    },
    {
        "name": "interview_prep",
        "description": (
            "Write an interview prep sheet for a company (and role if known): their "
            "interview stages, likely questions with answers drawn from Mo's CV, questions "
            "to ask, what to revise. Use for 'prepare me for my interview at X'. Then offer "
            "a practice interview: play their interviewer and ask one question at a time, "
            "with one follow-up when an answer is thin or strong. After each answer: what "
            "landed, what to sharpen, and a stronger opening built only from his CV; say "
            "so when he reuses the same story for a second question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "role": {"type": "string"}
            },
            "required": ["company"]
        }
    },
    {
        "name": "learn_skill",
        "description": (
            "Save a new SkillForge skill -- a reusable natural-language routine. Use when Mo "
            "says 'learn this as a skill', 'save this as a routine', or accepts a skill proposal. "
            "Instructions must be complete, self-contained steps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short skill name, e.g. 'morning nvda check'."},
                "instructions": {"type": "string", "description": "Complete numbered steps to execute."},
                "trigger_phrases": {"type": "string", "description": "Optional comma-separated phrases that should trigger this skill."}
            },
            "required": ["name", "instructions"]
        }
    },
    {
        "name": "list_skills",
        "description": "List all learned skills with run counts and schedule status.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "run_skill",
        "description": (
            "Fetch a skill's instructions for immediate execution. Use when Mo names a skill or "
            "uses one of its trigger phrases ('focus time', 'good morning', 'quiz me', ...). "
            "EXECUTE the returned steps right away with your other tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name or the trigger phrase Mo used."}
            },
            "required": ["name"]
        }
    },
    {
        "name": "delete_skill",
        "description": "Delete a learned skill (also unschedules it).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]
        }
    },
    {
        "name": "schedule_skill",
        "description": (
            "Turn a skill into a recurring automation, ONLY after Mo confirms. "
            "at_time='HH:MM' makes the first run wait until that time (then repeats every every_hours)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "every_hours": {"type": "number", "description": "Repeat interval in hours (24 = daily)."},
                "at_time": {"type": "string", "description": "Optional HH:MM for the first run, e.g. '09:00'."}
            },
            "required": ["name"]
        }
    },
    {
        "name": "unschedule_skill",
        "description": "Stop a skill's automatic schedule (the skill itself is kept).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]
        }
    },
    {
        "name": "skill_proposals",
        "description": (
            "Mine Mo's recent conversations for repeated asks and list pending skill proposals. "
            "Use when Mo asks for skill suggestions or responds to a proactive proposal."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "dismiss_skill_proposal",
        "description": "Dismiss a skill proposal by id so it is never suggested again.",
        "input_schema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"]
        }
    },
    {
        "name": "import_routines",
        "description": (
            "Scan Mo's calendar (recurring events, next 2 weeks) and gym program, turn each "
            "recurring commitment into a skill, and schedule it automatically. Use when Mo says "
            "'import my routines', 'turn my tasks into skills', or 'automate my week'."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "sync_skills_to_claude",
        "description": (
            "Export all learned skills as Claude Code skills (.claude/skills/fager-*) so the "
            "same routines are runnable from Claude Code. Use when Mo says 'sync my skills' "
            "or after importing/learning several skills."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "start_mission",
        "description": (
            "Start a multi-step background mission. Use when Mo gives a BIG "
            "multi-part or long-running goal that cannot be finished in one "
            "response ('research X, compare Y, then write a summary', 'plan and "
            "execute Z overnight'). Decompose the goal into 2-8 concrete ordered "
            "steps yourself. Steps run in the background, about one per minute."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall goal in one sentence."},
                "steps": {"type": "string", "description": "Ordered steps, one per line. Each step must be self-contained and executable. Prefix a line with '&' when it is INDEPENDENT of the previous step -- independent steps run together in the same cycle (faster)."}
            },
            "required": ["goal", "steps"]
        }
    },
    {
        "name": "mission_status",
        "description": "Report the current mission's progress ('how is the mission going', 'mission status').",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "cancel_mission",
        "description": "Cancel the running mission when Mo asks to stop it.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "usage_report",
        "description": (
            "Report El Fager's own Claude API usage and cost. Use when Mo asks "
            "'what did you cost me', 'how much have you spent', 'api usage', "
            "'your running costs', 'what has the job hunt cost'. Includes the "
            "job hunt's spend for the window and since it started. "
            "days=1 for today, 7 for the week."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Window in days (default 1)."}
            }
        }
    },
]

def _slim_tools(tools: list) -> list:
    slimmed = []
    for tool in tools:
        schema = tool.get("input_schema", {"type": "object", "properties": {}})
        slim_props = {
            k: {sk: sv for sk, sv in v.items() if sk in ("type", "enum", "items")}
            for k, v in schema.get("properties", {}).items()
        }
        slim_schema: dict = {"type": "object", "properties": slim_props}
        if "required" in schema:
            slim_schema["required"] = schema["required"]
        slimmed.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": slim_schema,
        })
    return slimmed


_SLIM_TOOLS = _slim_tools(TOOLS)

# ── Dynamic tool injection ────────────────────────────────────────────────────
# Only the tool names that are always included regardless of query topic.
_CORE_NAMES: frozenset[str] = frozenset({
    "web_search", "fetch_page", "open_web_search", "open_comet",
    "youtube_search", "youtube_latest",
    "translate_text", "wikipedia_lookup",
    "convert_currency", "get_exchange_rates", "resolve_doi",
    "get_weather", "get_weather_forecast", "get_hourly_weather",
    "get_news", "get_all_headlines", "search_news", "read_news_article",
    "get_prayer_times",
    "remember_fact", "forget_topic", "what_do_you_know", "list_facts",
    "set_reminder", "list_reminders", "cancel_reminder",
    "get_battery_status", "get_clipboard_history",
    "analyze_screen", "ocr_screenshot",
    "screen_agent", "browser_agent",
    "research_agent", "file_agent", "health_agent",
    "run_skill", "list_skills", "learn_skill",
    # Background tasks and mission steps carry no keywords, and may need Mo.
    "ask_mo",
    # A yes can arrive in a turn with no history (typed turns reset after
    # each one), so the way to act on a staged action is always on offer.
    "confirm_staged_action", "cancel_staged_action",
})

_TOOL_GROUP_NAMES: dict[str, frozenset[str]] = {
    "files": frozenset({
        "file_search", "open_file", "read_file_content", "open_app", "run_command",
        "create_folder", "rename_file", "copy_file", "move_file", "delete_file", "list_folder",
    }),
    "clipboard": frozenset({
        "get_clipboard", "set_clipboard", "get_clipboard_history",
    }),
    "productivity": frozenset({
        "list_calendar_events", "create_calendar_event", "update_calendar_event",
        "delete_calendar_event", "confirm_calendar_delete",
        "list_emails", "read_email", "search_emails", "send_email",
        "confirm_send_email", "reply_email", "confirm_reply_email",
        "list_email_templates", "get_email_template", "save_email_template", "delete_email_template",
        "list_tasks", "add_task", "complete_task", "delete_task",
        "start_pomodoro", "stop_pomodoro", "list_pomodoros",
        "enable_focus_mode", "disable_focus_mode",
        "save_flashcards",
    }),
    "mouse": frozenset({
        "mouse_move", "mouse_click", "mouse_double_click", "mouse_drag", "mouse_scroll",
        "type_text", "press_key", "hotkey", "get_mouse_position", "screenshot_coords",
    }),
    "window": frozenset({
        "list_windows", "get_active_window", "switch_to_window", "minimize_window",
        "maximize_window", "restore_window", "close_window", "resize_window",
        "move_window", "snap_window", "read_window_text",
    }),
    "browser": frozenset({
        "browser_is_open", "browser_open", "browser_navigate", "browser_click", "browser_type",
        "browser_get_text", "browser_get_title", "browser_screenshot", "browser_fill_form",
        "browser_submit", "browser_wait", "browser_scroll", "browser_close",
        "browser_back", "browser_get_links", "browser_select",
    }),
    "media": frozenset({
        "play_music", "pause_music", "next_track", "what_playing", "set_volume", "spotify_status",
    }),
    "system": frozenset({
        "set_system_volume", "get_system_volume", "mute_system", "set_brightness",
        "get_process_info", "kill_process",
    }),
    "messaging": frozenset({
        "prepare_whatsapp_message", "confirm_whatsapp_send", "add_whatsapp_contact",
        "list_whatsapp_contacts", "delete_whatsapp_contact", "send_whatsapp_to_number",
        "send_telegram", "get_telegram_messages", "add_telegram_contact", "list_telegram_contacts",
    }),
    "cloud": frozenset({
        "search_notion", "read_notion_page", "append_to_notion", "create_notion_page",
        "search_drive", "share_drive_file", "upload_to_drive",
        "read_doc", "append_to_doc", "read_sheet", "append_sheet_row",
        "list_repos", "list_issues", "list_prs", "get_repo_info",
        "get_youtube_transcript",
    }),
    "obsidian": frozenset({
        "search_vault", "ask_vault", "read_note", "list_notes",
        "create_note", "append_to_note", "append_to_daily_note",
        "delete_note", "rename_note", "move_note",
        "get_backlinks", "get_outgoing_links", "list_vault_tags",
        "search_vault_by_tag", "index_vault",
    }),
    "image": frozenset({
        "generate_image", "generate_variation", "list_generated_images", "open_image",
        "delete_image", "clear_all_images", "search_images", "get_image_info",
        "favorite_image", "list_favorite_images", "set_as_wallpaper", "copy_image_path",
    }),
    "journal": frozenset({
        "save_journal_entry", "read_journal", "edit_journal_entry", "append_to_entry",
        "rename_entry_title", "list_journal_entries", "most_recent_entry", "random_memory",
        "search_journal", "search_by_tag", "list_tags", "mood_summary", "delete_journal_entry",
        "pin_entry", "list_pinned_entries", "get_journal_stats", "journal_streak",
        "weekly_summary", "read_journal_range", "export_journal", "copy_journal_to_clipboard",
        "read_conversation", "search_conversations", "conversation_stats", "export_conversation",
        "spending_insights", "journal_insights", "productivity_insights", "weekly_report",
        "mood_trend", "top_tools",
        "log_expense", "get_expense_summary", "list_recent_expenses",
        "daily_activity", "streak_stats",
        "cash_flow_summary", "revenue_insights", "profit_loss_report",
    }),
    "finance": frozenset({
        "log_income", "get_income_summary", "list_recent_income",
        "create_invoice", "send_invoice", "mark_invoice_paid",
        "list_invoices", "get_invoice", "delete_invoice",
        "set_budget", "list_budgets", "delete_budget",
        "set_savings_goal", "update_savings_progress", "list_savings_goals", "delete_savings_goal",
        "cash_flow_summary", "revenue_insights", "profit_loss_report",
    }),
    "bizmath": frozenset({
        "startup_metrics", "burn_runway", "break_even", "margin_analysis",
        "roi_calc", "dcf_value", "valuation_multiples",
        "loan_payment", "compound_growth", "cagr_calc",
    }),
    "macro": frozenset({
        "add_schedule", "list_schedules", "remove_schedule", "pause_schedule", "resume_schedule",
        "run_now", "get_schedule", "schedule_history", "reschedule",
        "pause_all_schedules", "resume_all_schedules", "scheduler_status", "job_history",
        "schedule_stats", "clear_history",
        "create_macro", "run_macro", "list_macros", "delete_macro", "get_macro",
        "clone_macro", "edit_macro", "add_step", "remove_step", "macro_stats",
    }),
    "code": frozenset({
        "run_python", "run_powershell", "execute_file", "run_bash", "run_node",
        "run_with_stdin", "run_with_args", "check_syntax", "benchmark", "format_python",
        "pip_install", "pip_uninstall", "pip_show", "list_packages", "get_python_info",
        "create_script", "get_script", "list_scripts", "run_script", "delete_script",
        "run_in_background", "list_background", "kill_background", "open_in_editor",
    }),
    "system_health": frozenset({
        "system_health", "get_disk_space", "get_cpu_usage", "get_ram_usage",
        "get_system_uptime", "get_top_processes",
    }),
    "network": frozenset({
        "check_internet", "get_network_status", "ping", "internet_speed",
        "get_local_ip", "get_public_ip",
    }),
    "pdf": frozenset({
        "create_pdf", "merge_pdfs", "split_pdf", "compress_pdf", "pdf_info", "pdf_to_text",
    }),
    "capture": frozenset({
        "start_recording", "stop_recording", "recording_status", "take_snapshot",
    }),
    "printer": frozenset({
        "list_printers", "print_file", "print_text", "get_default_printer", "set_default_printer",
    }),
    "archive": frozenset({
        "zip_files", "unzip_archive", "list_archive", "add_to_archive",
    }),
    "image_edit": frozenset({
        "resize_image", "crop_image", "convert_image", "compress_image", "rotate_image",
    }),
    "units": frozenset({
        "convert_units", "list_unit_categories",
    }),
    "git": frozenset({
        "git_status", "git_log", "git_diff", "git_add", "git_commit", "git_push", "git_pull",
    }),
    "dev_utils": frozenset({
        "hash_text", "encode_base64", "decode_base64",
        "url_encode", "url_decode",
        "generate_password", "generate_uuid", "generate_qr",
    }),
    "autonomous_tasks": frozenset({
        "add_autonomous_task", "list_autonomous_tasks", "delete_autonomous_task",
    }),
    "notifications": frozenset({
        "send_notification", "notification_status",
    }),
    "skills": frozenset({
        "learn_skill", "list_skills", "run_skill", "delete_skill",
        "schedule_skill", "unschedule_skill", "skill_proposals",
        "dismiss_skill_proposal", "import_routines", "sync_skills_to_claude",
    }),
    "usage": frozenset({"usage_report"}),
    "missions": frozenset({"start_mission", "mission_status", "cancel_mission"}),
    "jobs": frozenset({
        "job_search_agent", "prepare_applications", "review_applications",
        "approve_applications", "application_status", "check_application_replies",
        "import_cv", "set_application_answer", "application_settings", "interview_prep",
        "graduate_programmes", "find_referrals", "referral_list", "mark_referral",
        "import_linkedin_connections", "mark_followed_up", "evaluate_job", "skill_gaps",
        "retry_applications",
    }),
}

_GROUP_TRIGGERS: dict[str, list[str]] = {
    "files":       ["open file", "read file", "find file", "search file", "open app",
                    "open application", "run command", "folder", "directory", ".exe",
                    "create folder", "make folder", "new folder", "rename", "copy file",
                    "move file", "delete file", "list folder", "what's in", "show me the files"],
    "clipboard":   ["clipboard", "what did i copy", "paste", "copied"],
    "productivity":["calendar", "event", "meeting", "email", "mail", "inbox", "task",
                    "todo", "pomodoro", "focus mode", "flashcard", "appointment",
                    "schedule meeting", "unread", "compose", "template",
                    "block distractions", "study mode"],
    "mouse":       ["click", "type", "press", "drag", "scroll", "move mouse", "right click", "double click", "keyboard", "hotkey", "ctrl+"],
    "window":      ["window", "switch to", "bring up", "minimize", "maximize", "close app", "snap", "side by side", "half screen", "windows open",
                    "what does it say", "what's written", "read me that", "read that"],
    "browser":     ["browser", "open chrome", "navigate to", "go to website", "fill form", "click the button", "log in to", "scrape", "automate", "web page", "website"],
    "media":       ["play", "music", "song", "pause music", "skip", "next track", "spotify",
                    "what's playing", "volume up", "volume down"],
    "system":      ["volume", "brightness", "mute", "battery", "process", "cpu",
                    "ram", "memory usage", "kill process", "task manager"],
    "messaging":   ["whatsapp", "telegram", "send to", "text to", "message to",
                    "wa "],
    "cloud":       ["notion", "drive", "github", "repo", "repository", "google doc",
                    "spreadsheet", "sheet", "upload to", "issue", "pull request", "youtube",
                    "transcript", "summarise video", "summarize video"],
    "obsidian":    ["obsidian", "vault", "my notes", "note that", "take a note",
                    "make a note", "new note", "daily note", "wikilink", "backlink",
                    "what did i write", "note it down", "add to my note", "in my note",
                    "what did i think about", "what do my notes", "linked to",
                    "links to", "rename note", "delete note", "move note",
                    "tagged", "my tags", "reindex", "note about"],
    "image":       ["image", "picture", "photo", "generate", "draw", "wallpaper",
                    "illustration"],
    "journal":     ["journal", "diary", "mood", "expense", "spent", "spend", "spending",
                    "analytics", "weekly report", "weekly summary", "history", "conversation",
                    "what did we", "what did i do", "talked about", "insight", "trend",
                    "yesterday", "what happened", "daily activity", "recap", "streak",
                    "consistency", "how consistent",
                    "income", "revenue", "invoice", "savings", "cash flow", "profit"],
    "finance":     ["invoice", "invoices", "client", "bill ", "billing",
                    "income", "earned", "got paid", "payment received",
                    "cash flow", "profit", "loss", "p&l",
                    "budget", "over budget", "savings", "savings goal", "save up",
                    "financial report", "how much did i make", "how much i made",
                    "log income", "received payment",
                    ],
    "bizmath":     ["startup", "saas", "mrr", "arr", "ltv", "cac", "churn", "burn rate",
                    "runway", "break even", "break-even", "margin analysis",
                    "roi", "return on investment", "dcf", "valuation", "multiple",
                    "revenue multiple", "ebitda", "loan payment", "emi", "monthly payment",
                    "compound interest", "compound growth", "future value", "cagr",
                    "annual growth", "business valuation", "startup metrics",
                    "payback period", "ltv cac", "rule of 72"],
    "macro":       ["routine", "macro", "morning", "night", "sabaho", "tes7a",
                    "study mode", "chill mode", "workout", "gaming", "weekend", "commute",
                    "every day", "every week", "add schedule", "my schedule", "scheduled"],
    "code":        ["run python", "run code", "execute", "python code", "install package",
                    "pip install", "javascript", "powershell", "bash", "benchmark",
                    "check syntax", "format code", "script", "run script", "background job",
                    "run in background", "run this", "calculate", "code:"],
    "system_health": ["disk space", "how much ram", "cpu usage", "system health", "cpu percent",
                      "is my laptop ok", "how's my pc", "what's using memory", "uptime",
                      "top processes", "what's eating", "memory usage", "system status",
                      ],
    "network":     ["internet", "wifi", "wi-fi", "connection", "ping", "network", "speed test",
                    "ip address", "connected", "my ip", "public ip", "local ip"],
    "pdf":         ["pdf", "merge pdf", "compress pdf", "create pdf", "split pdf",
                    "pdf pages", "PDF", "extract pages", "combine pdf", "pdf info",
                    "pdf text"],
    "capture":     ["record screen", "screen record", "record my screen", "start recording",
                    "stop recording", "capture video", "screen video", "recording",
                    "snapshot"],
    "printer":     ["print", "printer", "printing", "print this", "print file"],
    "archive":     ["zip", "unzip", "archive", "compress files", "extract", ".zip",
                    "pack files", "bundle files", "zipped"],
    "image_edit":  ["resize image", "crop image", "compress image", "convert image",
                    "rotate image", "make image smaller", "image to jpg", "image to png",
                    "flip image", "shrink image", "scale image"],
    "units":       ["convert", "how many", "how much is", "degrees celsius", "degrees fahrenheit",
                    "kilometers to miles", "kg to lbs", "lbs to kg", "meters to feet",
                    "temperature convert", "inches to", "gallons to", "megabytes to",
                    "gigabytes to"],
    "git":         ["git status", "git commit", "git push", "git pull", "git log", "git diff",
                    "git add", "commit my changes", "push my code", "what changed in git",
                    "stage files", "local repo", "git repo", "version control"],
    "dev_utils":   ["hash", "md5", "sha256", "base64", "encode base64", "decode base64",
                    "url encode", "url decode", "generate password", "random password",
                    "strong password", "uuid", "qr code", "qr ", "generate qr"],
    "autonomous_tasks": [
        "queue", "add task for yourself", "do this for me", "do this later",
        "autonomous task", "background task", "my queued tasks", "what tasks do you have",
        "what tasks have you", "tasks queued", "cancel task", "remove task",
        "el fager do", "execute later", "run this later", "task queue",
        "you do this", "do x for me", "tonight please",
    ],
    "notifications": [
        "send my phone", "ping me", "notify my phone", "send notification",
        "whatsapp notification", "whatsapp alert", "phone notification", "notification status",
        "is whatsapp set up", "set up notifications", "phone alerts",
        "how do i set up notifications", "push to my phone", "send me a whatsapp",
        "ping me on whatsapp", "whatsapp me",
    ],
    "skills": [
        "skill", "skills", "learn this", "make it a skill", "save this as",
        "automate", "automation", "automatically", "routine", "schedule this",
        "every morning", "every day", "every night", "every week",
        "stop doing", "suggestions", "proposal",
    ],
    "usage": [
        "cost me", "you cost", "api usage", "api cost", "your cost",
        "how much have you spent", "token usage", "running costs",
        "what did you spend", "job hunt cost", "job search cost",
        "applications cost", "spent on the job",
    ],
    "missions": [
        "mission", "missions", "big task", "multi-step", "step by step plan",
        "overnight", "work through", "plan and execute", "and then", "then write",
    ],
    "jobs": [
        "job", "jobs", "internship", "internships", "vacancy", "vacancies",
        "hiring", "wuzzuf", "bayt", "forasna", "job search", "apply for",
        "applications", "job application", "my cv", "resume", "interview", "interviews",
        "big 4", "big four", "recruiter", "military status", "expected salary",
        "graduate program", "graduate programme", "referral", "referrals",
        "briefing", "good morning", "sabah el kheir", "start my day",
        "refer me", "linkedin", "connections", "followed up", "follow up", "follow-up",
        "opening", "openings", "position", "positions", "posting", "career", "careers",
        "sap", "erp", "odoo", "what should i learn", "skills am i missing",
        "skill gap", "skill gaps", "retry", "signed in", "is the job hunt ready",
    ],
}

# Build a name→slim_tool lookup once for O(1) filtering
_SLIM_BY_NAME: dict[str, dict] = {t["name"]: t for t in _SLIM_TOOLS}

# ── Skill permissions (Settings → Skills) ────────────────────────────────────
# The six surfaces the user can switch off, and the tools each one owns. A
# disabled skill's tools are never sent with the request, and a tool that
# isn't in the request cannot be called — that is the whole gate.
_ALL_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOLS)

SKILL_TOOLS: dict[str, frozenset[str]] = {
    # Template CRUD is local text, so it stays available with Gmail off.
    "gmail": frozenset(
        n for n in _ALL_TOOL_NAMES if "email" in n and "template" not in n
    ),
    "whatsapp": frozenset(n for n in _ALL_TOOL_NAMES if "whatsapp" in n),
    "calendar": frozenset(n for n in _ALL_TOOL_NAMES if "calendar" in n),
    "todoist": frozenset({"add_task", "complete_task", "delete_task", "list_tasks"}),
    "browser": frozenset(n for n in _ALL_TOOL_NAMES if n.startswith("browser_")),
    "screen": frozenset({
        "analyze_screen", "ocr_screenshot", "screenshot_coords", "screen_agent",
    }),
}

# What each of those is called, and what it lets El Fager do, for the Cockpit's
# SKILLS tab and Settings. Paired with SKILL_TOOLS by a test, so a new surface
# has to be named here too.
SKILL_LABELS: dict[str, tuple[str, str]] = {
    "gmail":    ("Gmail", "Read, draft and send email"),
    "whatsapp": ("WhatsApp", "Draft and send to your chats"),
    "calendar": ("Calendar", "Read your days and add events"),
    "todoist":  ("Todoist", "Tasks and to-dos"),
    "browser":  ("Browser", "Drive Comet for you"),
    "screen":   ("Screen", "Read what is on your screen"),
}


def _disabled_tool_names() -> set[str]:
    """Tools belonging to skills switched off in Settings → Skills.

    Read per request rather than cached, like core.sound.enabled(), so a
    toggle takes hold on the very next turn instead of after a restart.
    """
    try:
        with open("data/settings.json", encoding="utf-8") as f:
            disabled = json.load(f).get("skills_disabled", [])
    except Exception:
        return set()
    names: set[str] = set()
    for skill in disabled:
        names |= SKILL_TOOLS.get(skill, frozenset())
    return names


def _boundaried(phrases, trailing: bool = True):
    """Compile phrases into one alternation that matches on word boundaries.

    Plain substring matching was firing groups on fragments of unrelated
    words: "roi" inside "android" pulled in the finance tools, "play" inside
    "display" pulled in media, "ping" inside "sleeping" pulled in network.

    The boundary assertions are added per edge, and only where the phrase's
    own edge is a word character. Seven triggers begin or end on punctuation
    or a space — "ctrl+", "wa ", ".exe", "code:", "bill ", "qr ", ".zip" — and
    a blanket word boundary would stop every one of them ever matching again.

    trailing=False is for stem hints like "analyz" and "summar", which are
    meant to catch "analyze" and "summarise" and so must stay open-ended.
    """
    parts = []
    for phrase in phrases:
        if not phrase:
            continue
        pattern = re.escape(phrase)
        if phrase[0].isalnum() or phrase[0] == "_":
            pattern = r"(?<!\w)" + pattern
        if trailing and (phrase[-1].isalnum() or phrase[-1] == "_"):
            pattern = pattern + r"(?!\w)"
        parts.append(pattern)
    return re.compile("|".join(parts), re.IGNORECASE)


# Compiled once at import. Rebuilding 32 alternations per turn would cost more
# than the substring scan it replaces.
_GROUP_RE = {group: _boundaried(keywords)
             for group, keywords in _GROUP_TRIGGERS.items()}


def _select_tools(message: str, history: list | None = None) -> list:
    """Return a slimmed tool list relevant to the user's message. Recent user
    turns from the conversation also count, so multi-turn follow-ups like
    'and its P/E?' keep the tool groups the conversation already activated."""
    parts = [message]
    if history:
        parts.extend(
            m["content"] for m in history[-8:]
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        )
    msg = " ".join(parts).lower()
    names: set[str] = set(_CORE_NAMES)
    for group, pattern in _GROUP_RE.items():
        if pattern.search(msg):
            names.update(_TOOL_GROUP_NAMES[group])
    names -= _disabled_tool_names()
    return [t for t in _SLIM_TOOLS if t["name"] in names]


_MAX_TOOL_ITERATIONS = 15

# A confirm sends only what Mo already had in front of him when he spoke. The
# model once re-staged an expired email and confirmed it in the same turn.
_CONFIRM_TOOLS = frozenset({
    "confirm_staged_action", "confirm_send_email", "confirm_reply_email",
    "confirm_whatsapp_send", "confirm_calendar_delete",
})
_UNSEEN_CONFIRM = (
    "NOT SENT — nothing went out. Mo hasn't seen this draft yet, so it can't "
    "be confirmed in the same turn it was staged. Do not tell Mo it was sent. "
    "Tell him the draft is ready and ask him to say yes."
)
# Approving the job-application batch sends it under Mo's name. A job hunt
# may run as a background turn: it prepares the batch, only Mo approves.
_MO_ONLY_TOOLS = frozenset({"approve_applications"})
_BACKGROUND_REFUSAL = (
    "NOT APPROVED — only Mo can approve applications, in his own conversation "
    "or on the review page. Tell him the batch is ready for his review."
)

_HISTORY_WINDOW = 24  # max messages (12 exchanges) sent per request


def _cairo_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Africa/Cairo"))


def _window_history(hist: list) -> list:
    """Last _HISTORY_WINDOW messages, trimmed so the slice never opens on an
    assistant turn (the API requires the first message to be a user turn)."""
    messages = list(hist)[-_HISTORY_WINDOW:]
    while messages and messages[0]["role"] != "user":
        messages = messages[1:]
    return messages


class Brain:
    def __init__(self, profile: dict, memory=None):
        self.client = anthropic.Anthropic()
        self.profile = profile
        self.memory = memory
        self.conversation_history: list[dict] = []
        self._offline_mode = False
        # Tool names selected so far this conversation. The Anthropic cache
        # prefix runs tools -> system -> messages, so a changing tools array
        # invalidates the whole cached prefix. Holding the set and only ever
        # adding to it keeps the prefix stable across a conversation.
        self._turn_tool_names: "set[str] | None" = None
        try:
            _sf = "data/settings.json"
            _s = json.loads(open(_sf, encoding="utf-8").read()) if os.path.exists(_sf) else {}
            self._model: str = _s.get("model", "claude-sonnet-5")
            self._fast_model: str = _s.get("fast_model", "claude-haiku-4-5-20251001")
            self._fast_path_enabled: bool = _s.get("fast_path_enabled", True)
        except Exception:
            self._model = "claude-sonnet-5"
            self._fast_model = "claude-haiku-4-5-20251001"
            self._fast_path_enabled = True
        try:
            from core.conversation_log import ConversationLogger
            self._logger = ConversationLogger()
        except Exception:
            self._logger = None

    # Turns containing any of these route to the full model — they benefit from
    # deeper reasoning. Everything short and simple goes to the fast model.
    _COMPLEX_HINTS = (
        "why", "how come", "explain", "analyz", "analys", "compare", "summar",
        "research", "debug", "step by step", "pros and cons", "trade-off",
        "tradeoff", "strateg", "refactor", "translate", "write a", "write me",
        "essay", "brainstorm", "in detail",
    )
    # trailing=False: several of these are stems — "summar" has to reach
    # "summarise", "analyz" has to reach "analyze".
    _COMPLEX_RE = _boundaried(_COMPLEX_HINTS, trailing=False)

    # Confirmation replies must reach the full model — they're expected to
    # trigger a confirm_* tool call (send email/WhatsApp, delete event), and
    # Haiku has proven unreliable at actually calling the tool instead of
    # just replying conversationally (it hallucinated "draft expired" without
    # ever calling confirm_whatsapp_send). These stay short, so the full
    # model still returns fast for them.
    _CONFIRM_HINTS = (
        "yes", "yeah", "yep", "sure", "confirm", "go ahead", "do it",
        "send it", "cancel", "no don't", "don't send",
    )
    # Whole words, both edges: "sure" inside "measure" and "yes" inside "eyes"
    # were routing ordinary turns to the expensive model.
    _CONFIRM_RE = _boundaried(_CONFIRM_HINTS)

    def _select_model(self, user_message: str) -> str:
        """Pick the model for this turn. Short, simple turns go to the fast
        (Haiku) model for near-instant replies; longer, reasoning-heavy, or
        confirmation turns use the full model. Toggle with fast_path_enabled
        in settings.json. Chosen once per turn so the whole tool loop stays
        on one model."""
        if not self._fast_path_enabled:
            return self._model
        msg = (user_message or "").strip().lower()
        if self._CONFIRM_RE.search(msg):
            return self._model
        # Anything said while an action is armed may be a confirm or a cancel
        # in words the list above misses ("never mind") — Haiku once claimed
        # a cancel without calling the tool.
        from core import staging
        if staging.current() is not None:
            return self._model
        if len(msg.split()) > 18:
            return self._model
        if self._COMPLEX_RE.search(msg):
            return self._model
        return self._fast_model

    _DISPATCH_RETRY_DELAYS = (1.0, 3.0)  # 2 retries with backoff on transient errors

    def _dispatch_tool(self, name: str, tool_input: dict) -> str:
        """Dispatch a tool call, retrying transient failures (429/5xx/network).

        Non-transient errors are caught inside _dispatch_tool_once and
        returned as a normal "Tool error (...)" string without retrying.
        """
        from core import progress

        # Every tool call passes through here, so this is where the step
        # ledger is written — the surfaces show what a turn is doing.
        step = progress.step_started(name)
        last_exc: Exception | None = None
        for delay in self._DISPATCH_RETRY_DELAYS:
            try:
                result = self._dispatch_tool_once(name, tool_input)
                progress.step_finished(step, ok=True)
                return result
            except Exception as e:
                last_exc = e
                time.sleep(delay)
        try:
            result = self._dispatch_tool_once(name, tool_input)
            progress.step_finished(step, ok=True)
            return result
        except Exception as e:
            last_exc = e
        progress.step_finished(step, ok=False)
        attempts = len(self._DISPATCH_RETRY_DELAYS) + 1
        return f"Tool error ({name}): {last_exc} (failed after {attempts} attempts)"

    def _dispatch_tool_once(self, name: str, tool_input: dict) -> str:
        from tools.files_tool import search_files, open_file, read_file_content
        from tools.system_tool import open_app, run_command
        from tools.clipboard_tool import get_clipboard_text, set_clipboard_text
        from tools.web_tool import web_search, fetch_page
        from tools.reminder_tool import set_reminder, list_reminders, cancel_reminder

        try:
            if name == "file_search":
                return search_files(**tool_input)
            elif name == "open_file":
                return open_file(**tool_input)
            elif name == "read_file_content":
                return read_file_content(**tool_input)
            elif name == "open_app":
                return open_app(**tool_input)
            elif name == "run_command":
                return run_command(**tool_input)
            elif name == "get_clipboard":
                return get_clipboard_text()
            elif name == "set_clipboard":
                return set_clipboard_text(**tool_input)
            elif name == "web_search":
                return web_search(**tool_input)
            elif name == "fetch_page":
                return fetch_page(**tool_input)
            elif name == "set_reminder":
                return set_reminder(**tool_input)
            elif name == "list_reminders":
                return list_reminders()
            elif name == "cancel_reminder":
                return cancel_reminder(**tool_input)
            elif name == "remember_fact":
                if self.memory is not None:
                    return self.memory.store_fact(**tool_input)
                return "[Memory not available]"
            elif name == "forget_topic":
                if self.memory is not None:
                    return self.memory.forget_topic(**tool_input)
                return "[Memory not available]"
            elif name == "what_do_you_know":
                if self.memory is not None:
                    return self.memory.get_memory_summary()
                return "[Memory not available]"
            elif name == "list_facts":
                if self.memory is not None:
                    category = tool_input.get("category") or None
                    facts = self.memory.get_all_facts(category)
                    if not facts:
                        label = f"No {category} facts" if category else "No facts"
                        return f"{label} stored."
                    return "\n".join(f"[{f['category']}] {f['content']}" for f in facts)
                return "[Memory not available]"
            elif name == "list_calendar_events":
                from tools import calendar_tool
                return calendar_tool.list_events(
                    tool_input.get("time_range", "today"),
                    tool_input.get("max_results", 10),
                )
            elif name == "create_calendar_event":
                from tools import calendar_tool
                return calendar_tool.create_event(**tool_input)
            elif name == "update_calendar_event":
                from tools import calendar_tool
                return calendar_tool.update_event(**tool_input)
            elif name == "delete_calendar_event":
                from tools import calendar_tool
                return calendar_tool.delete_event(**tool_input)
            elif name == "confirm_calendar_delete":
                from tools import calendar_tool
                return calendar_tool.confirm_delete_event()
            elif name == "list_emails":
                from tools import gmail_tool
                return gmail_tool.list_messages(
                    tool_input.get("n", 5),
                    tool_input.get("unread_only", True),
                    tool_input.get("query", ""),
                )
            elif name == "read_email":
                from tools import gmail_tool
                return gmail_tool.read_message(tool_input["msg_id"])
            elif name == "search_emails":
                from tools import gmail_tool
                return gmail_tool.search_messages(
                    tool_input["query"],
                    tool_input.get("n", 10),
                )
            elif name == "send_email":
                from tools import gmail_tool
                return gmail_tool.send_message(**tool_input)
            elif name == "confirm_send_email":
                from tools import gmail_tool
                return gmail_tool.confirm_send_message()
            elif name == "reply_email":
                from tools import gmail_tool
                return gmail_tool.reply_to_message(**tool_input)
            elif name == "confirm_reply_email":
                from tools import gmail_tool
                return gmail_tool.confirm_reply_message()
            elif name == "prepare_whatsapp_message":
                from tools import whatsapp_tool
                return whatsapp_tool.prepare_whatsapp_message(
                    tool_input["contact_name"], tool_input["message"],
                    contact_name_arabic=tool_input.get("contact_name_arabic"),
                )
            elif name == "confirm_whatsapp_send":
                from tools import whatsapp_tool
                return whatsapp_tool.confirm_whatsapp_send()
            elif name == "confirm_staged_action":
                from core import staging
                return staging.confirm()
            elif name == "cancel_staged_action":
                from core import staging
                if staging.current() is None:
                    return "Nothing is staged."
                staging.cancel()
                return "Cancelled — nothing was sent."
            elif name == "add_whatsapp_contact":
                from tools import whatsapp_tool
                return whatsapp_tool.add_contact(
                    tool_input["name"], tool_input["phone_number"]
                )
            elif name == "list_whatsapp_contacts":
                from tools import whatsapp_tool
                return whatsapp_tool.list_contacts()
            elif name == "delete_whatsapp_contact":
                from tools import whatsapp_tool
                return whatsapp_tool.delete_contact(tool_input["name"])
            elif name == "send_whatsapp_to_number":
                from tools import whatsapp_tool
                return whatsapp_tool.send_to_number(
                    tool_input["phone"], tool_input["message"]
                )
            # Phase 4B — Notion
            elif name == "search_notion":
                from tools import notion_tool
                return notion_tool.search_notion(
                    tool_input["query"], tool_input.get("n", 5)
                )
            elif name == "read_notion_page":
                from tools import notion_tool
                return notion_tool.read_notion_page(tool_input["page_id"])
            elif name == "append_to_notion":
                from tools import notion_tool
                return notion_tool.append_to_notion(
                    tool_input["page_id"], tool_input["text"]
                )
            elif name == "create_notion_page":
                from tools import notion_tool
                return notion_tool.create_notion_page(
                    tool_input["title"],
                    tool_input.get("content", ""),
                    tool_input.get("parent_page_id"),
                )
            # Obsidian — local Markdown vault
            elif name == "search_vault":
                from tools import obsidian_tool
                return obsidian_tool.search_vault(
                    tool_input["query"], tool_input.get("n", 5)
                )
            elif name == "read_note":
                from tools import obsidian_tool
                return obsidian_tool.read_note(tool_input["name"])
            elif name == "create_note":
                from tools import obsidian_tool
                return obsidian_tool.create_note(
                    tool_input["title"],
                    tool_input.get("content", ""),
                    tool_input.get("folder"),
                )
            elif name == "append_to_note":
                from tools import obsidian_tool
                return obsidian_tool.append_to_note(
                    tool_input["name"], tool_input["text"]
                )
            elif name == "append_to_daily_note":
                from tools import obsidian_tool
                return obsidian_tool.append_to_daily_note(tool_input["text"])
            elif name == "list_notes":
                from tools import obsidian_tool
                return obsidian_tool.list_notes(
                    tool_input.get("folder"), tool_input.get("n", 30)
                )
            elif name == "ask_vault":
                from tools import obsidian_tool
                return obsidian_tool.ask_vault(
                    tool_input["query"], tool_input.get("n", 5)
                )
            elif name == "index_vault":
                from tools import obsidian_tool
                return obsidian_tool.index_vault(tool_input.get("rebuild", False))
            elif name == "delete_note":
                from tools import obsidian_tool
                return obsidian_tool.delete_note(tool_input["name"])
            elif name == "rename_note":
                from tools import obsidian_tool
                return obsidian_tool.rename_note(
                    tool_input["name"], tool_input["new_title"]
                )
            elif name == "move_note":
                from tools import obsidian_tool
                return obsidian_tool.move_note(
                    tool_input["name"], tool_input["folder"]
                )
            elif name == "get_backlinks":
                from tools import obsidian_tool
                return obsidian_tool.get_backlinks(
                    tool_input["name"], tool_input.get("n", 20)
                )
            elif name == "get_outgoing_links":
                from tools import obsidian_tool
                return obsidian_tool.get_outgoing_links(tool_input["name"])
            elif name == "list_vault_tags":
                from tools import obsidian_tool
                return obsidian_tool.list_vault_tags(tool_input.get("n", 40))
            elif name == "search_vault_by_tag":
                from tools import obsidian_tool
                return obsidian_tool.search_vault_by_tag(
                    tool_input["tag"], tool_input.get("n", 20)
                )
            # Phase 4C — Todoist
            elif name == "list_tasks":
                from tools import todoist_tool
                return todoist_tool.list_tasks(tool_input.get("filter", "today"))
            elif name == "add_task":
                from tools import todoist_tool
                return todoist_tool.add_task(
                    tool_input["content"],
                    tool_input.get("due_string"),
                    tool_input.get("priority", 1),
                )
            elif name == "complete_task":
                from tools import todoist_tool
                return todoist_tool.complete_task(tool_input["search_term"])
            elif name == "delete_task":
                from tools import todoist_tool
                return todoist_tool.delete_task(tool_input["search_term"])
            # Phase 4E — Google Workspace
            elif name == "search_drive":
                from tools import gdrive_tool
                return gdrive_tool.search_drive(tool_input["query"], tool_input.get("n", 10))
            elif name == "share_drive_file":
                from tools import gdrive_tool
                return gdrive_tool.share_drive_file(tool_input["file_id_or_name"])
            elif name == "upload_to_drive":
                from tools import gdrive_tool
                return gdrive_tool.upload_to_drive(
                    tool_input["local_path"], tool_input.get("folder_id")
                )
            elif name == "read_doc":
                from tools import gdrive_tool
                return gdrive_tool.read_doc(tool_input["doc_id"])
            elif name == "append_to_doc":
                from tools import gdrive_tool
                return gdrive_tool.append_to_doc(tool_input["doc_id"], tool_input["text"])
            elif name == "read_sheet":
                from tools import gdrive_tool
                return gdrive_tool.read_sheet(
                    tool_input["spreadsheet_id"], tool_input.get("range_name", "Sheet1")
                )
            elif name == "append_sheet_row":
                from tools import gdrive_tool
                return gdrive_tool.append_sheet_row(
                    tool_input["spreadsheet_id"],
                    tool_input["sheet_name"],
                    tool_input["values"],
                )
            # Phase 5A — Email Templates
            elif name == "list_email_templates":
                from tools import email_templates_tool
                return email_templates_tool.list_email_templates()
            elif name == "get_email_template":
                from tools import email_templates_tool
                return email_templates_tool.get_email_template(tool_input["name"])
            elif name == "save_email_template":
                from tools import email_templates_tool
                return email_templates_tool.save_email_template(
                    tool_input["name"],
                    tool_input["description"],
                    tool_input["subject"],
                    tool_input["body"],
                    tool_input.get("to_hint", ""),
                )
            elif name == "delete_email_template":
                from tools import email_templates_tool
                return email_templates_tool.delete_email_template(tool_input["name"])
            # Phase 5B — YouTube Transcript
            elif name == "get_youtube_transcript":
                from tools import youtube_tool
                return youtube_tool.get_youtube_transcript(
                    tool_input["url"],
                    tool_input.get("language"),
                )
            # Phase 5C — Pomodoro Timer
            elif name == "convert_currency":
                from tools import currency_tool
                return currency_tool.convert_currency(
                    tool_input["amount"],
                    tool_input["from_currency"],
                    tool_input["to_currency"],
                )
            elif name == "get_exchange_rates":
                from tools import currency_tool
                return currency_tool.get_exchange_rates(tool_input.get("base", "EGP"))
            # Phase 6B — Expenses
            elif name == "log_expense":
                from tools import expense_tool
                return expense_tool.log_expense(
                    tool_input["amount"],
                    tool_input.get("currency", "EGP"),
                    tool_input.get("category", "other"),
                    tool_input.get("description", ""),
                )
            elif name == "get_expense_summary":
                from tools import expense_tool
                return expense_tool.get_expense_summary(tool_input.get("period", "week"))
            elif name == "list_recent_expenses":
                from tools import expense_tool
                return expense_tool.list_recent_expenses(tool_input.get("n", 10))
            # Phase 6C — Translation
            elif name == "translate_text":
                from tools import translation_tool
                return translation_tool.translate_text(
                    tool_input["text"],
                    tool_input["target_language"],
                    tool_input.get("source_language", "auto"),
                )
            # Phase 6D — Wikipedia
            elif name == "wikipedia_lookup":
                from tools import wikipedia_tool
                return wikipedia_tool.wikipedia_lookup(
                    tool_input["query"],
                    tool_input.get("language", "en"),
                )
            # Phase 6E — Prayer Times
            elif name == "get_prayer_times":
                from tools import prayer_tool
                return prayer_tool.get_prayer_times(tool_input.get("date"))
            # Phase 6F — Focus Mode
            elif name == "enable_focus_mode":
                from tools import focus_tool
                return focus_tool.enable_focus_mode(
                    tool_input.get("hours", 2.0),
                    tool_input.get("sites"),
                )
            elif name == "disable_focus_mode":
                from tools import focus_tool
                return focus_tool.disable_focus_mode()
            # Phase 6G — Clipboard History
            elif name == "get_clipboard_history":
                from tools import clipboard_history_tool
                return clipboard_history_tool.get_clipboard_history(tool_input.get("n", 10))
            # Phase 6H — System Controls
            elif name == "set_system_volume":
                from tools import system_control_tool
                return system_control_tool.set_system_volume(tool_input["level"])
            elif name == "get_system_volume":
                from tools import system_control_tool
                return system_control_tool.get_system_volume()
            elif name == "mute_system":
                from tools import system_control_tool
                return system_control_tool.mute_system()
            elif name == "set_brightness":
                from tools import system_control_tool
                return system_control_tool.set_brightness(tool_input["level"])
            elif name == "get_battery_status":
                from tools import system_control_tool
                return system_control_tool.get_battery_status()
            # Phase 6I — Process Manager
            elif name == "get_process_info":
                from tools import process_tool
                return process_tool.get_process_info(tool_input.get("name"))
            elif name == "kill_process":
                from tools import process_tool
                return process_tool.kill_process(tool_input["name"])
            # Phase 6A — Telegram
            elif name == "send_telegram":
                from tools import telegram_tool
                return telegram_tool.send_telegram(tool_input["contact_name"], tool_input["message"])
            elif name == "get_telegram_messages":
                from tools import telegram_tool
                return telegram_tool.get_telegram_messages(tool_input.get("n", 10))
            elif name == "add_telegram_contact":
                from tools import telegram_tool
                return telegram_tool.add_telegram_contact(tool_input["name"], tool_input["chat_id"])
            elif name == "list_telegram_contacts":
                from tools import telegram_tool
                return telegram_tool.list_telegram_contacts()
            # Phase 6B — News
            elif name == "get_news":
                from tools import news_tool
                return news_tool.get_news(tool_input.get("category", "world"), tool_input.get("n", 5))
            elif name == "get_all_headlines":
                from tools import news_tool
                return news_tool.get_all_headlines(tool_input.get("n", 2))
            elif name == "search_news":
                from tools import news_tool
                return news_tool.search_news(tool_input["query"], tool_input.get("n", 8))
            elif name == "read_news_article":
                from tools import news_tool
                return news_tool.read_news_article(tool_input["index_or_url"])
            # Phase 6C — Weather
            elif name == "get_weather":
                from tools import weather_tool
                return weather_tool.get_weather(tool_input.get("city", "Cairo"))
            elif name == "get_weather_forecast":
                from tools import weather_tool
                return weather_tool.get_weather_forecast(tool_input.get("city", "Cairo"), tool_input.get("days", 3))
            elif name == "get_hourly_weather":
                from tools import weather_tool
                return weather_tool.get_hourly_weather(tool_input.get("city", "Cairo"), tool_input.get("hours", 12))
            # Phase 6D — Image Generation
            elif name == "save_journal_entry":
                from tools import journal_tool
                return journal_tool.save_journal_entry(
                    tool_input["text"],
                    tool_input.get("title"),
                    tool_input.get("mood"),
                    tool_input.get("tags"),
                )
            elif name == "read_journal":
                from tools import journal_tool
                return journal_tool.read_journal(tool_input.get("date_str"))
            elif name == "list_journal_entries":
                from tools import journal_tool
                return journal_tool.list_journal_entries(tool_input.get("n", 7))
            elif name == "edit_journal_entry":
                from tools import journal_tool
                return journal_tool.edit_journal_entry(
                    tool_input["entry_number"],
                    tool_input["new_text"],
                    tool_input.get("date_str"),
                )
            elif name == "append_to_entry":
                from tools import journal_tool
                return journal_tool.append_to_entry(
                    tool_input["entry_number"],
                    tool_input["additional_text"],
                    tool_input.get("date_str"),
                )
            elif name == "most_recent_entry":
                from tools import journal_tool
                return journal_tool.most_recent_entry()
            elif name == "search_journal":
                from tools import journal_tool
                return journal_tool.search_journal(tool_input["query"])
            elif name == "search_by_tag":
                from tools import journal_tool
                return journal_tool.search_by_tag(tool_input["tag"])
            elif name == "list_tags":
                from tools import journal_tool
                return journal_tool.list_tags()
            elif name == "mood_summary":
                from tools import journal_tool
                return journal_tool.mood_summary(tool_input.get("days", 7))
            elif name == "delete_journal_entry":
                from tools import journal_tool
                return journal_tool.delete_journal_entry(
                    tool_input["entry_number"],
                    tool_input.get("date_str"),
                )
            elif name == "get_journal_stats":
                from tools import journal_tool
                return journal_tool.get_journal_stats()
            elif name == "journal_streak":
                from tools import journal_tool
                return journal_tool.journal_streak()
            elif name == "weekly_summary":
                from tools import journal_tool
                return journal_tool.weekly_summary(tool_input.get("weeks_ago", 0))
            elif name == "read_journal_range":
                from tools import journal_tool
                return journal_tool.read_journal_range(
                    tool_input["start_date"],
                    tool_input.get("end_date"),
                )
            elif name == "run_python":
                from tools.code_tool import run_python
                return run_python(tool_input["code"], tool_input.get("timeout", 30))
            elif name == "run_powershell":
                from tools.code_tool import run_powershell
                return run_powershell(tool_input["command"], tool_input.get("timeout", 30))
            elif name == "execute_file":
                from tools.code_tool import execute_file
                return execute_file(tool_input["path"], tool_input.get("timeout", 60))
            elif name == "run_bash":
                from tools.code_tool import run_bash
                return run_bash(tool_input["command"], tool_input.get("timeout", 30))
            elif name == "pip_install":
                from tools.code_tool import pip_install
                return pip_install(tool_input["package"], tool_input.get("upgrade", False))
            elif name == "list_packages":
                from tools.code_tool import list_packages
                return list_packages(tool_input.get("filter_str", ""))
            elif name == "get_python_info":
                from tools.code_tool import get_python_info
                return get_python_info()
            elif name == "run_with_stdin":
                from tools.code_tool import run_with_stdin
                return run_with_stdin(tool_input["code"], tool_input["stdin_input"], tool_input.get("timeout", 30))
            elif name == "run_with_args":
                from tools.code_tool import run_with_args
                return run_with_args(tool_input["path"], tool_input.get("args", ""), tool_input.get("timeout", 60))
            elif name == "pip_uninstall":
                from tools.code_tool import pip_uninstall
                return pip_uninstall(tool_input["package"])
            elif name == "pip_show":
                from tools.code_tool import pip_show
                return pip_show(tool_input["package"])
            elif name == "create_script":
                from tools.code_tool import create_script
                return create_script(tool_input["name"], tool_input["code"], tool_input.get("language", "python"))
            elif name == "get_script":
                from tools.code_tool import get_script
                return get_script(tool_input["name"])
            elif name == "list_scripts":
                from tools.code_tool import list_scripts
                return list_scripts()
            elif name == "run_script":
                from tools.code_tool import run_script
                return run_script(tool_input["name"], tool_input.get("args", ""), tool_input.get("timeout", 60))
            elif name == "delete_script":
                from tools.code_tool import delete_script
                return delete_script(tool_input["name"])
            elif name == "read_conversation":
                from tools.history_tool import read_conversation
                return read_conversation(tool_input.get("date_str", "today"))
            elif name == "search_conversations":
                from tools.history_tool import search_conversations
                return search_conversations(tool_input["query"], tool_input.get("n", 10))
            elif name == "conversation_stats":
                from tools.history_tool import conversation_stats
                return conversation_stats()
            elif name == "export_conversation":
                from tools.history_tool import export_conversation
                return export_conversation(tool_input.get("date_str", "today"))
            # Phase 7E — Analytics
            elif name == "spending_insights":
                from tools.analytics_tool import spending_insights
                return spending_insights(tool_input.get("period", "month"))
            elif name == "journal_insights":
                from tools.analytics_tool import journal_insights
                return journal_insights(tool_input.get("period", "month"))
            elif name == "productivity_insights":
                from tools.analytics_tool import productivity_insights
                return productivity_insights(tool_input.get("period", "week"))
            elif name == "weekly_report":
                from tools.analytics_tool import weekly_report
                return weekly_report()
            elif name == "mood_trend":
                from tools.analytics_tool import mood_trend
                return mood_trend(tool_input.get("days", 14))
            elif name == "top_tools":
                from tools.analytics_tool import top_tools
                return top_tools(tool_input.get("n", 10))
            elif name == "daily_activity":
                from tools.analytics_tool import daily_activity
                return daily_activity(tool_input.get("date", ""))
            elif name == "streak_stats":
                from tools.analytics_tool import streak_stats
                return streak_stats()
            # Phase 8 — Income
            elif name == "log_income":
                from tools import income_tool
                return income_tool.log_income(
                    tool_input["amount"],
                    tool_input.get("currency", "EGP"),
                    tool_input.get("category", "other"),
                    tool_input.get("source", ""),
                    tool_input.get("description", ""),
                    tool_input.get("invoice_id", ""),
                )
            elif name == "get_income_summary":
                from tools import income_tool
                return income_tool.get_income_summary(tool_input.get("period", "month"))
            elif name == "list_recent_income":
                from tools import income_tool
                return income_tool.list_recent_income(tool_input.get("n", 10))
            # Phase 8 — Invoices
            elif name == "create_invoice":
                from tools import invoice_tool
                return invoice_tool.create_invoice(
                    tool_input["client"],
                    tool_input["amount"],
                    tool_input["description"],
                    tool_input["due_date"],
                    tool_input.get("currency", "EGP"),
                    tool_input.get("notes", ""),
                )
            elif name == "send_invoice":
                from tools import invoice_tool
                return invoice_tool.send_invoice(tool_input["invoice_id"])
            elif name == "mark_invoice_paid":
                from tools import invoice_tool
                return invoice_tool.mark_invoice_paid(
                    tool_input["invoice_id"],
                    tool_input.get("paid_date", ""),
                )
            elif name == "list_invoices":
                from tools import invoice_tool
                return invoice_tool.list_invoices(tool_input.get("status_filter", "all"))
            elif name == "get_invoice":
                from tools import invoice_tool
                return invoice_tool.get_invoice(tool_input["invoice_id"])
            elif name == "delete_invoice":
                from tools import invoice_tool
                return invoice_tool.delete_invoice(tool_input["invoice_id"])
            # Phase 8 — Budget
            elif name == "set_budget":
                from tools import budget_tool
                return budget_tool.set_budget(
                    tool_input["category"],
                    tool_input["limit"],
                    tool_input.get("period", "month"),
                    tool_input.get("currency", "EGP"),
                )
            elif name == "list_budgets":
                from tools import budget_tool
                return budget_tool.list_budgets()
            elif name == "delete_budget":
                from tools import budget_tool
                return budget_tool.delete_budget(
                    tool_input["category"],
                    tool_input.get("period", "month"),
                )
            # Phase 8 — Savings goals
            elif name == "set_savings_goal":
                from tools import budget_tool
                return budget_tool.set_savings_goal(
                    tool_input["name"],
                    tool_input["target"],
                    tool_input.get("currency", "EGP"),
                    tool_input.get("deadline", ""),
                    tool_input.get("notes", ""),
                )
            elif name == "update_savings_progress":
                from tools import budget_tool
                return budget_tool.update_savings_progress(
                    tool_input["name"],
                    tool_input["amount_saved"],
                )
            elif name == "list_savings_goals":
                from tools import budget_tool
                return budget_tool.list_savings_goals()
            elif name == "delete_savings_goal":
                from tools import budget_tool
                return budget_tool.delete_savings_goal(tool_input["name"])
            # Phase 8 — Finance analytics
            elif name == "cash_flow_summary":
                from tools.analytics_tool import cash_flow_summary
                return cash_flow_summary(tool_input.get("period", "month"))
            elif name == "revenue_insights":
                from tools.analytics_tool import revenue_insights
                return revenue_insights(tool_input.get("period", "month"))
            elif name == "profit_loss_report":
                from tools.analytics_tool import profit_loss_report
                return profit_loss_report(tool_input.get("period", "month"))
            # ── Spotify ───────────────────────────────────────────────────────────
            elif name == "youtube_search":
                from tools.youtube_tool import youtube_search
                return youtube_search(tool_input["query"])
            elif name == "youtube_latest":
                from tools.youtube_tool import youtube_latest
                return youtube_latest(tool_input["channel"])
            elif name == "open_web_search":
                from tools.web_tool import open_web_search
                return open_web_search(tool_input["task"])
            elif name == "open_comet":
                from tools.comet_tool import open_comet
                return open_comet(tool_input.get("url", ""))
            elif name == "play_music":
                from tools.spotify_tool import play_music
                return play_music(tool_input["query"])
            elif name == "pause_music":
                from tools.spotify_tool import pause_music
                return pause_music()
            elif name == "next_track":
                from tools.spotify_tool import next_track
                return next_track()
            elif name == "what_playing":
                from tools.spotify_tool import what_playing
                return what_playing()
            elif name == "set_volume":
                from tools.spotify_tool import set_volume
                return set_volume(tool_input["level"])
            elif name == "spotify_status":
                from tools.spotify_tool import spotify_status
                return spotify_status()
            # ── Image generation ───────────────────────────────────────────────────
            elif name == "generate_image":
                from tools.image_tool import generate_image
                return generate_image(
                    tool_input["prompt"],
                    tool_input.get("size", "1024x1024"),
                    tool_input.get("quality", "standard"),
                    tool_input.get("style", "vivid"),
                )
            elif name == "generate_variation":
                from tools.image_tool import generate_variation
                return generate_variation(
                    tool_input["index_or_filename"],
                    tool_input["changes"],
                    tool_input.get("size"),
                    tool_input.get("quality"),
                    tool_input.get("style"),
                )
            elif name == "list_generated_images":
                from tools.image_tool import list_generated_images
                return list_generated_images(tool_input.get("n", 10))
            elif name == "open_image":
                from tools.image_tool import open_image
                return open_image(tool_input["index_or_filename"])
            elif name == "delete_image":
                from tools.image_tool import delete_image
                return delete_image(tool_input["index_or_filename"])
            elif name == "clear_all_images":
                from tools.image_tool import clear_all_images
                return clear_all_images()
            elif name == "search_images":
                from tools.image_tool import search_images
                return search_images(tool_input["query"])
            elif name == "get_image_info":
                from tools.image_tool import get_image_info
                return get_image_info(tool_input["index_or_filename"])
            elif name == "favorite_image":
                from tools.image_tool import favorite_image
                return favorite_image(tool_input["index_or_filename"])
            elif name == "list_favorite_images":
                from tools.image_tool import list_favorite_images
                return list_favorite_images()
            elif name == "set_as_wallpaper":
                from tools.image_tool import set_as_wallpaper
                return set_as_wallpaper(tool_input["index_or_filename"])
            elif name == "copy_image_path":
                from tools.image_tool import copy_image_path
                return copy_image_path(tool_input["index_or_filename"])
            # ── Pomodoro ───────────────────────────────────────────────────────────
            elif name == "start_pomodoro":
                from tools.pomodoro_tool import start_pomodoro
                return start_pomodoro(tool_input.get("minutes", 25), tool_input.get("label", "Focus session"))
            elif name == "stop_pomodoro":
                from tools.pomodoro_tool import stop_pomodoro
                return stop_pomodoro(tool_input.get("label"))
            elif name == "list_pomodoros":
                from tools.pomodoro_tool import list_pomodoros
                return list_pomodoros()
            # ── Flashcards ─────────────────────────────────────────────────────────
            elif name == "save_flashcards":
                from tools.flashcard_tool import save_flashcards
                return save_flashcards(
                    tool_input["cards"],
                    tool_input.get("output_path", "data/flashcards.csv"),
                    tool_input.get("deck_name", "El Fager"),
                )
            # ── Citation ───────────────────────────────────────────────────────────
            elif name == "resolve_doi":
                from tools.citation_tool import resolve_doi
                return resolve_doi(tool_input["doi"])
            # ── GitHub ─────────────────────────────────────────────────────────────
            elif name == "list_repos":
                from tools.github_tool import list_repos
                return list_repos(tool_input.get("username"), tool_input.get("n", 10))
            elif name == "list_issues":
                from tools.github_tool import list_issues
                return list_issues(tool_input["repo"], tool_input.get("n", 10))
            elif name == "list_prs":
                from tools.github_tool import list_prs
                return list_prs(tool_input["repo"], tool_input.get("n", 10))
            elif name == "get_repo_info":
                from tools.github_tool import get_repo_info
                return get_repo_info(tool_input["repo"])
            # ── Scheduler ──────────────────────────────────────────────────────────
            elif name == "add_schedule":
                from tools.scheduler_tool import add_schedule
                return add_schedule(
                    tool_input["name"], tool_input["when"],
                    tool_input.get("tool_name"), tool_input.get("args"),
                    tool_input.get("macro_name"), tool_input.get("description"),
                    tool_input.get("run_on_add", False),
                )
            elif name == "list_schedules":
                from tools.scheduler_tool import list_schedules
                return list_schedules(tool_input.get("filter", "all"))
            elif name == "remove_schedule":
                from tools.scheduler_tool import remove_schedule
                return remove_schedule(tool_input["name"])
            elif name == "pause_schedule":
                from tools.scheduler_tool import pause_schedule
                return pause_schedule(tool_input["name"])
            elif name == "resume_schedule":
                from tools.scheduler_tool import resume_schedule
                return resume_schedule(tool_input["name"])
            elif name == "run_now":
                from tools.scheduler_tool import run_now
                return run_now(tool_input["name"])
            elif name == "get_schedule":
                from tools.scheduler_tool import get_schedule
                return get_schedule(tool_input["name"])
            elif name == "schedule_history":
                from tools.scheduler_tool import schedule_history
                return schedule_history(tool_input.get("n", 10))
            elif name == "reschedule":
                from tools.scheduler_tool import reschedule
                return reschedule(tool_input["name"], tool_input["when"])
            elif name == "pause_all_schedules":
                from tools.scheduler_tool import pause_all_schedules
                return pause_all_schedules()
            elif name == "resume_all_schedules":
                from tools.scheduler_tool import resume_all_schedules
                return resume_all_schedules()
            elif name == "scheduler_status":
                from tools.scheduler_tool import scheduler_status
                return scheduler_status()
            elif name == "job_history":
                from tools.scheduler_tool import job_history
                return job_history(tool_input["name"], tool_input.get("n", 10))
            elif name == "schedule_stats":
                from tools.scheduler_tool import schedule_stats
                return schedule_stats()
            elif name == "clear_history":
                from tools.scheduler_tool import clear_history
                return clear_history()
            # ── Macros ─────────────────────────────────────────────────────────────
            elif name == "create_macro":
                from tools.macro_tool import create_macro
                return create_macro(
                    tool_input["name"], tool_input["steps"],
                    tool_input.get("description", ""), tool_input.get("tags"),
                )
            elif name == "run_macro":
                from tools.macro_tool import run_macro
                return run_macro(tool_input["name"])
            elif name == "list_macros":
                from tools.macro_tool import list_macros
                return list_macros(tool_input.get("tag"))
            elif name == "delete_macro":
                from tools.macro_tool import delete_macro
                return delete_macro(tool_input["name"])
            elif name == "get_macro":
                from tools.macro_tool import get_macro
                return get_macro(tool_input["name"])
            elif name == "clone_macro":
                from tools.macro_tool import clone_macro
                return clone_macro(tool_input["source"], tool_input["new_name"])
            elif name == "edit_macro":
                from tools.macro_tool import edit_macro
                return edit_macro(
                    tool_input["name"], tool_input["step_index"],
                    tool_input.get("tool"), tool_input.get("args"),
                    tool_input.get("type"), tool_input.get("text"),
                    tool_input.get("seconds"),
                )
            elif name == "add_step":
                from tools.macro_tool import add_step
                return add_step(
                    tool_input["name"],
                    tool_input.get("tool"), tool_input.get("args"),
                    tool_input.get("type"), tool_input.get("text"),
                    tool_input.get("seconds"), tool_input.get("title"),
                    tool_input.get("message"),
                )
            elif name == "remove_step":
                from tools.macro_tool import remove_step
                return remove_step(tool_input["name"], tool_input["step_index"])
            elif name == "macro_stats":
                from tools.macro_tool import macro_stats
                return macro_stats()
            # ── Code extras ────────────────────────────────────────────────────────
            elif name == "run_node":
                from tools.code_tool import run_node
                return run_node(tool_input["code"], tool_input.get("timeout", 30))
            elif name == "check_syntax":
                from tools.code_tool import check_syntax
                return check_syntax(tool_input["code"])
            elif name == "benchmark":
                from tools.code_tool import benchmark
                return benchmark(tool_input["code"], tool_input.get("runs", 5), tool_input.get("timeout", 60))
            elif name == "format_python":
                from tools.code_tool import format_python
                return format_python(tool_input["code"])
            elif name == "run_in_background":
                from tools.code_tool import run_in_background
                return run_in_background(tool_input["command"], tool_input["label"], tool_input.get("language", "python"))
            elif name == "list_background":
                from tools.code_tool import list_background
                return list_background()
            elif name == "kill_background":
                from tools.code_tool import kill_background
                return kill_background(tool_input["label"])
            elif name == "open_in_editor":
                from tools.code_tool import open_in_editor
                return open_in_editor(tool_input["path"])
            # ── Journal extras ─────────────────────────────────────────────────────
            elif name == "random_memory":
                from tools.journal_tool import random_memory
                return random_memory()
            elif name == "rename_entry_title":
                from tools.journal_tool import rename_entry_title
                return rename_entry_title(
                    tool_input["entry_number"], tool_input["new_title"],
                    tool_input.get("date_str"),
                )
            elif name == "pin_entry":
                from tools.journal_tool import pin_entry
                return pin_entry(tool_input["entry_number"], tool_input.get("date_str"))
            elif name == "list_pinned_entries":
                from tools.journal_tool import list_pinned_entries
                return list_pinned_entries()
            elif name == "export_journal":
                from tools.journal_tool import export_journal
                return export_journal(tool_input.get("start_date"), tool_input.get("end_date"))
            elif name == "copy_journal_to_clipboard":
                from tools.journal_tool import copy_journal_to_clipboard
                return copy_journal_to_clipboard(tool_input.get("date_str"))
            # ── Mouse & Keyboard ──────────────────────────────────────────────────
            elif name == "mouse_move":
                from tools.mouse_tool import mouse_move
                return mouse_move(tool_input["x"], tool_input["y"], tool_input.get("duration", 0.3))
            elif name == "mouse_click":
                from tools.mouse_tool import mouse_click
                return mouse_click(tool_input.get("x"), tool_input.get("y"), tool_input.get("button", "left"), tool_input.get("clicks", 1))
            elif name == "mouse_double_click":
                from tools.mouse_tool import mouse_double_click
                return mouse_double_click(tool_input.get("x"), tool_input.get("y"))
            elif name == "mouse_drag":
                from tools.mouse_tool import mouse_drag
                return mouse_drag(tool_input["x1"], tool_input["y1"], tool_input["x2"], tool_input["y2"], tool_input.get("duration", 0.5))
            elif name == "mouse_scroll":
                from tools.mouse_tool import mouse_scroll
                return mouse_scroll(tool_input["amount"], tool_input.get("x"), tool_input.get("y"))
            elif name == "type_text":
                from tools.mouse_tool import type_text
                return type_text(tool_input["text"], tool_input.get("interval", 0.03))
            elif name == "press_key":
                from tools.mouse_tool import press_key
                return press_key(tool_input["key"])
            elif name == "hotkey":
                from tools.mouse_tool import hotkey
                return hotkey(*tool_input["keys"])
            elif name == "get_mouse_position":
                from tools.mouse_tool import get_mouse_position
                return get_mouse_position()
            elif name == "screenshot_coords":
                from tools.mouse_tool import screenshot_coords
                return screenshot_coords(tool_input["x"], tool_input["y"], tool_input.get("width", 100), tool_input.get("height", 100))
            # ── Window Management ──────────────────────────────────────────────────
            elif name == "list_windows":
                from tools.window_tool import list_windows
                return list_windows(tool_input.get("filter", ""))
            elif name == "get_active_window":
                from tools.window_tool import get_active_window
                return get_active_window()
            elif name == "read_window_text":
                from tools import window_tool
                return window_tool.read_window_text(tool_input.get("title", ""))
            elif name == "switch_to_window":
                from tools.window_tool import switch_to_window
                return switch_to_window(tool_input["title"])
            elif name == "minimize_window":
                from tools.window_tool import minimize_window
                return minimize_window(tool_input.get("title"))
            elif name == "maximize_window":
                from tools.window_tool import maximize_window
                return maximize_window(tool_input.get("title"))
            elif name == "restore_window":
                from tools.window_tool import restore_window
                return restore_window(tool_input.get("title"))
            elif name == "close_window":
                from tools.window_tool import close_window
                return close_window(tool_input["title"])
            elif name == "resize_window":
                from tools.window_tool import resize_window
                return resize_window(tool_input["title"], tool_input["width"], tool_input["height"])
            elif name == "move_window":
                from tools.window_tool import move_window
                return move_window(tool_input["title"], tool_input["x"], tool_input["y"])
            elif name == "snap_window":
                from tools.window_tool import snap_window
                return snap_window(tool_input["title"], tool_input["position"])
            # ── Browser Automation ─────────────────────────────────────────────────
            elif name == "browser_is_open":
                from tools.browser_tool import browser_is_open
                return browser_is_open()
            elif name == "browser_open":
                from tools.browser_tool import browser_open
                return browser_open(tool_input.get("url", ""), tool_input.get("headless", False))
            elif name == "browser_navigate":
                from tools.browser_tool import browser_navigate
                return browser_navigate(tool_input["url"])
            elif name == "browser_click":
                from tools.browser_tool import browser_click
                return browser_click(tool_input["selector_or_text"])
            elif name == "browser_type":
                from tools.browser_tool import browser_type
                return browser_type(tool_input["selector"], tool_input["text"], tool_input.get("clear", True))
            elif name == "browser_get_text":
                from tools.browser_tool import browser_get_text
                return browser_get_text(tool_input.get("selector", "body"))
            elif name == "browser_get_title":
                from tools.browser_tool import browser_get_title
                return browser_get_title()
            elif name == "browser_screenshot":
                from tools.browser_tool import browser_screenshot
                return browser_screenshot(tool_input.get("path"))
            elif name == "browser_fill_form":
                from tools.browser_tool import browser_fill_form
                return browser_fill_form(tool_input["fields"])
            elif name == "browser_submit":
                from tools.browser_tool import browser_submit
                return browser_submit(tool_input["selector"])
            elif name == "browser_wait":
                from tools.browser_tool import browser_wait
                return browser_wait(tool_input.get("seconds", 2))
            elif name == "browser_scroll":
                from tools.browser_tool import browser_scroll
                return browser_scroll(tool_input.get("direction", "down"), tool_input.get("amount", 3))
            elif name == "browser_close":
                from tools.browser_tool import browser_close
                return browser_close()
            elif name == "browser_back":
                from tools.browser_tool import browser_back
                return browser_back()
            elif name == "browser_get_links":
                from tools.browser_tool import browser_get_links
                return browser_get_links(tool_input.get("filter", ""))
            elif name == "browser_select":
                from tools.browser_tool import browser_select
                return browser_select(tool_input["selector"], tool_input["value"])
            # ── Screen vision ─────────────────────────────────────────────────
            elif name == "analyze_screen":
                from tools.screen_analysis_tool import analyze_screen
                return analyze_screen(tool_input.get("question", "What do you see on screen?"))
            # ── Business calculator ───────────────────────────────────────────
            elif name == "startup_metrics":
                from tools.business_calculator import startup_metrics
                return startup_metrics(
                    tool_input["mrr"], tool_input["churn_rate"], tool_input["cac"],
                    tool_input.get("avg_revenue_per_user", 0),
                )
            elif name == "burn_runway":
                from tools.business_calculator import burn_runway
                return burn_runway(tool_input["monthly_burn"], tool_input["cash"])
            elif name == "break_even":
                from tools.business_calculator import break_even
                return break_even(
                    tool_input["fixed_costs"],
                    tool_input["variable_cost_per_unit"],
                    tool_input["price_per_unit"],
                )
            elif name == "margin_analysis":
                from tools.business_calculator import margin_analysis
                return margin_analysis(
                    tool_input["revenue"], tool_input["cogs"],
                    tool_input["operating_expenses"],
                    tool_input.get("tax_rate", 22.5),
                )
            elif name == "roi_calc":
                from tools.business_calculator import roi_calc
                return roi_calc(
                    tool_input["investment"], tool_input["gross_return"],
                    tool_input.get("years", 1),
                )
            elif name == "dcf_value":
                from tools.business_calculator import dcf_value
                return dcf_value(
                    tool_input["cash_flows"], tool_input["discount_rate"],
                    tool_input.get("terminal_growth", 2.0),
                )
            elif name == "valuation_multiples":
                from tools.business_calculator import valuation_multiples
                return valuation_multiples(
                    tool_input.get("revenue", 0), tool_input.get("ebitda", 0),
                    tool_input.get("net_income", 0),
                    tool_input.get("rev_multiple", 0), tool_input.get("ebitda_multiple", 0),
                    tool_input.get("pe_multiple", 0),
                )
            elif name == "loan_payment":
                from tools.business_calculator import loan_payment
                return loan_payment(
                    tool_input["principal"], tool_input["annual_rate"], tool_input["months"]
                )
            elif name == "compound_growth":
                from tools.business_calculator import compound_growth
                return compound_growth(
                    tool_input["initial"], tool_input["annual_rate"], tool_input["years"],
                    tool_input.get("monthly_addition", 0),
                )
            elif name == "cagr_calc":
                from tools.business_calculator import cagr_calc
                return cagr_calc(
                    tool_input["start_value"], tool_input["end_value"], tool_input["years"]
                )
            # ── OCR Screenshot ────────────────────────────────────────────────
            elif name == "ocr_screenshot":
                from tools.screen_tool import ocr_screenshot
                return ocr_screenshot()
            # ── System Health ─────────────────────────────────────────────────
            elif name == "system_health":
                from tools.system_health_tool import system_health
                return system_health()
            elif name == "get_disk_space":
                from tools.system_health_tool import get_disk_space
                return get_disk_space(tool_input.get("path", "C:\\"))
            elif name == "get_cpu_usage":
                from tools.system_health_tool import get_cpu_usage
                return get_cpu_usage(tool_input.get("interval", 1))
            elif name == "get_ram_usage":
                from tools.system_health_tool import get_ram_usage
                return get_ram_usage()
            elif name == "get_system_uptime":
                from tools.system_health_tool import get_system_uptime
                return get_system_uptime()
            elif name == "get_top_processes":
                from tools.system_health_tool import get_top_processes
                return get_top_processes(tool_input.get("n", 5))
            # ── Network ───────────────────────────────────────────────────────
            elif name == "check_internet":
                from tools.network_tool import check_internet
                return check_internet()
            elif name == "get_network_status":
                from tools.network_tool import get_network_status
                return get_network_status()
            elif name == "ping":
                from tools.network_tool import ping
                return ping(tool_input["host"], tool_input.get("count", 4))
            elif name == "internet_speed":
                from tools.network_tool import internet_speed
                return internet_speed()
            elif name == "get_local_ip":
                from tools.network_tool import get_local_ip
                return get_local_ip()
            elif name == "get_public_ip":
                from tools.network_tool import get_public_ip
                return get_public_ip()
            # ── PDF ───────────────────────────────────────────────────────────
            elif name == "create_pdf":
                from tools.pdf_tool import create_pdf
                return create_pdf(
                    tool_input["text"], tool_input["output_path"],
                    tool_input.get("title", "")
                )
            elif name == "merge_pdfs":
                from tools.pdf_tool import merge_pdfs
                return merge_pdfs(tool_input["input_paths"], tool_input["output_path"])
            elif name == "split_pdf":
                from tools.pdf_tool import split_pdf
                return split_pdf(
                    tool_input["input_path"], tool_input["pages"], tool_input["output_path"]
                )
            elif name == "compress_pdf":
                from tools.pdf_tool import compress_pdf
                return compress_pdf(
                    tool_input["input_path"], tool_input.get("output_path")
                )
            elif name == "pdf_info":
                from tools.pdf_tool import pdf_info
                return pdf_info(tool_input["path"])
            elif name == "pdf_to_text":
                from tools.pdf_tool import pdf_to_text
                return pdf_to_text(tool_input["path"], tool_input.get("pages"))
            # ── Screen Recording ──────────────────────────────────────────────
            elif name == "start_recording":
                from tools.screen_record_tool import start_recording
                return start_recording(
                    tool_input.get("output_path"), tool_input.get("fps", 15)
                )
            elif name == "stop_recording":
                from tools.screen_record_tool import stop_recording
                return stop_recording()
            elif name == "recording_status":
                from tools.screen_record_tool import recording_status
                return recording_status()
            elif name == "take_snapshot":
                from tools.screen_record_tool import take_snapshot
                return take_snapshot(tool_input.get("output_path"))
            # ── Printer ───────────────────────────────────────────────────────
            elif name == "list_printers":
                from tools.printer_tool import list_printers
                return list_printers()
            elif name == "print_file":
                from tools.printer_tool import print_file
                return print_file(tool_input["path"], tool_input.get("printer"))
            elif name == "print_text":
                from tools.printer_tool import print_text
                return print_text(
                    tool_input["text"],
                    tool_input.get("title", "El Fager"),
                    tool_input.get("printer"),
                )
            elif name == "get_default_printer":
                from tools.printer_tool import get_default_printer
                return get_default_printer()
            elif name == "set_default_printer":
                from tools.printer_tool import set_default_printer
                return set_default_printer(tool_input["name"])
            # ── File operations ───────────────────────────────────────────────
            elif name == "create_folder":
                from tools.file_ops_tool import create_folder
                return create_folder(tool_input["path"])
            elif name == "rename_file":
                from tools.file_ops_tool import rename_file
                return rename_file(tool_input["path"], tool_input["new_name"])
            elif name == "copy_file":
                from tools.file_ops_tool import copy_file
                return copy_file(tool_input["src"], tool_input["dst"])
            elif name == "move_file":
                from tools.file_ops_tool import move_file
                return move_file(tool_input["src"], tool_input["dst"])
            elif name == "delete_file":
                from tools.file_ops_tool import delete_file
                return delete_file(tool_input["path"])
            elif name == "list_folder":
                from tools.file_ops_tool import list_folder
                return list_folder(tool_input["path"], tool_input.get("show_hidden", False))
            # ── Archive ───────────────────────────────────────────────────────
            elif name == "zip_files":
                from tools.archive_tool import zip_files
                return zip_files(tool_input["paths"], tool_input["output_path"])
            elif name == "unzip_archive":
                from tools.archive_tool import unzip_archive
                return unzip_archive(tool_input["zip_path"], tool_input.get("output_dir"))
            elif name == "list_archive":
                from tools.archive_tool import list_archive
                return list_archive(tool_input["zip_path"])
            elif name == "add_to_archive":
                from tools.archive_tool import add_to_archive
                return add_to_archive(tool_input["zip_path"], tool_input["paths"])
            # ── Image editing ─────────────────────────────────────────────────
            elif name == "resize_image":
                from tools.image_edit_tool import resize_image
                return resize_image(tool_input["path"], tool_input["width"], tool_input.get("height"), tool_input.get("output"))
            elif name == "crop_image":
                from tools.image_edit_tool import crop_image
                return crop_image(tool_input["path"], tool_input["left"], tool_input["top"], tool_input["right"], tool_input["bottom"], tool_input.get("output"))
            elif name == "convert_image":
                from tools.image_edit_tool import convert_image
                return convert_image(tool_input["path"], tool_input["output_path"])
            elif name == "compress_image":
                from tools.image_edit_tool import compress_image
                return compress_image(tool_input["path"], tool_input.get("quality", 75), tool_input.get("output"))
            elif name == "rotate_image":
                from tools.image_edit_tool import rotate_image
                return rotate_image(tool_input["path"], tool_input["degrees"], tool_input.get("output"))
            # ── Unit conversion ───────────────────────────────────────────────
            elif name == "convert_units":
                from tools.unit_tool import convert_units
                return convert_units(tool_input["value"], tool_input["from_unit"], tool_input["to_unit"])
            elif name == "list_unit_categories":
                from tools.unit_tool import list_unit_categories
                return list_unit_categories()
            # ── Local git ─────────────────────────────────────────────────────
            elif name == "git_status":
                from tools.git_tool import git_status
                return git_status(tool_input.get("repo_path", "."))
            elif name == "git_log":
                from tools.git_tool import git_log
                return git_log(tool_input.get("repo_path", "."), tool_input.get("n", 10))
            elif name == "git_diff":
                from tools.git_tool import git_diff
                return git_diff(tool_input.get("repo_path", "."), tool_input.get("staged", False))
            elif name == "git_add":
                from tools.git_tool import git_add
                return git_add(tool_input["paths"], tool_input.get("repo_path", "."))
            elif name == "git_commit":
                from tools.git_tool import git_commit
                return git_commit(tool_input["message"], tool_input.get("repo_path", "."))
            elif name == "git_push":
                from tools.git_tool import git_push
                return git_push(tool_input.get("repo_path", "."), tool_input.get("remote", "origin"), tool_input.get("branch", ""))
            elif name == "git_pull":
                from tools.git_tool import git_pull
                return git_pull(tool_input.get("repo_path", "."), tool_input.get("remote", "origin"))
            # ── Developer utilities ───────────────────────────────────────────
            elif name == "hash_text":
                from tools.dev_utils_tool import hash_text
                return hash_text(tool_input["text"], tool_input.get("algorithm", "sha256"))
            elif name == "encode_base64":
                from tools.dev_utils_tool import encode_base64
                return encode_base64(tool_input["text"])
            elif name == "decode_base64":
                from tools.dev_utils_tool import decode_base64
                return decode_base64(tool_input["encoded"])
            elif name == "url_encode":
                from tools.dev_utils_tool import url_encode
                return url_encode(tool_input["text"])
            elif name == "url_decode":
                from tools.dev_utils_tool import url_decode
                return url_decode(tool_input["text"])
            elif name == "generate_password":
                from tools.dev_utils_tool import generate_password
                return generate_password(tool_input.get("length", 16), tool_input.get("include_symbols", True))
            elif name == "generate_uuid":
                from tools.dev_utils_tool import generate_uuid
                return generate_uuid()
            elif name == "generate_qr":
                from tools.dev_utils_tool import generate_qr
                return generate_qr(tool_input["text"], tool_input.get("output_path"))
            elif name == "add_autonomous_task":
                from tools.autonomous_task_tool import add_autonomous_task as _add_at
                return _add_at(**tool_input)
            elif name == "list_autonomous_tasks":
                from tools.autonomous_task_tool import list_autonomous_tasks as _list_at
                return _list_at()
            elif name == "delete_autonomous_task":
                from tools.autonomous_task_tool import delete_autonomous_task as _del_at
                return _del_at(**tool_input)
            elif name == "ask_mo":
                from core import ask_mo
                question = str(tool_input.get("question", "")).strip()
                if not question:
                    return "Error: no question."
                ask_mo.ask("task", question, question)
                return ("Asked Mo on WhatsApp. His answer will come back to you as a new "
                        "task; stop here for now.")
            elif name == "send_notification":
                from tools.notify_tool import send_notification as _send_notif
                return _send_notif(**tool_input)
            elif name == "notification_status":
                from tools.notify_tool import notification_status as _notif_status
                return _notif_status()
            elif name == "screen_agent":
                from core.agents.screen_agent import ScreenAgent
                return ScreenAgent().run(tool_input["task"])
            elif name == "browser_agent":
                from core.agents.browser_agent import BrowserAgent
                return BrowserAgent().run(tool_input["task"])
            elif name == "research_agent":
                from core.agents.research_agent import ResearchAgent
                return ResearchAgent().run(tool_input["task"])
            elif name == "file_agent":
                from core.agents.file_agent import FileAgent
                return FileAgent().run(tool_input["task"])
            elif name == "health_agent":
                from core.agents.health_agent import HealthAgent
                return HealthAgent().run(tool_input["task"])
            elif name == "job_search_agent":
                from core.agents.job_search_agent import JobSearchAgent
                return JobSearchAgent().run(tool_input.get("query", ""),
                                            show_all=tool_input.get("show_all", False))
            elif name in ("prepare_applications", "review_applications",
                          "approve_applications", "application_status",
                          "check_application_replies", "import_cv", "set_application_answer",
                          "application_settings", "interview_prep", "graduate_programmes",
                          "find_referrals", "referral_list", "mark_referral",
                          "import_linkedin_connections", "mark_followed_up",
                          "evaluate_job", "skill_gaps", "retry_applications"):
                from tools import career_tool
                return getattr(career_tool, name)(**tool_input)
            elif name == "learn_skill":
                from tools.skill_tool import learn_skill as _learn_sk
                return _learn_sk(**tool_input)
            elif name == "list_skills":
                from tools.skill_tool import list_skills as _list_sk
                return _list_sk()
            elif name == "run_skill":
                from tools.skill_tool import run_skill as _run_sk
                return _run_sk(**tool_input)
            elif name == "delete_skill":
                from tools.skill_tool import delete_skill as _del_sk
                return _del_sk(**tool_input)
            elif name == "schedule_skill":
                from tools.skill_tool import schedule_skill as _sched_sk
                return _sched_sk(**tool_input)
            elif name == "unschedule_skill":
                from tools.skill_tool import unschedule_skill as _unsched_sk
                return _unsched_sk(**tool_input)
            elif name == "skill_proposals":
                from tools.skill_tool import skill_proposals as _sk_props
                return _sk_props()
            elif name == "dismiss_skill_proposal":
                from tools.skill_tool import dismiss_skill_proposal as _dismiss_sk
                return _dismiss_sk(**tool_input)
            elif name == "import_routines":
                from tools.skill_tool import import_routines as _import_rt
                return _import_rt()
            elif name == "sync_skills_to_claude":
                from tools.skill_tool import sync_skills_to_claude as _sync_sk
                return _sync_sk()
            elif name == "usage_report":
                from tools.usage_tool import usage_report as _usage_rep
                return _usage_rep(**tool_input)
            elif name == "start_mission":
                from tools.mission_tool import start_mission as _start_mi
                return _start_mi(**tool_input)
            elif name == "mission_status":
                from tools.mission_tool import mission_status as _mi_status
                return _mi_status()
            elif name == "cancel_mission":
                from tools.mission_tool import cancel_mission as _cancel_mi
                return _cancel_mi()
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            if _is_transient_error(e):
                raise  # let _dispatch_tool retry with backoff
            return f"Tool error ({name}): {e}"

    def _try_play_music(self, message: str) -> str | None:
        """Handle a plain 'play <song>' without going to the model.

        Returns None for anything that isn't unambiguously a play command, so
        vaguer requests still get the tool loop's judgment.
        """
        try:
            from tools.spotify_tool import match_play_command, play_music
        except Exception:
            return None
        matched = match_play_command(message)
        if matched is None:
            return None
        query, arabic = matched
        result = play_music(query, arabic=arabic)
        # Bracketed results are the tool's error convention (not set up, no
        # Premium, no device) and nothing started playing. Let the tool loop
        # phrase those — they'd otherwise be read out as-is.
        if result.startswith("["):
            return None
        return result

    def _create_message(self, _telemetry_source: str, on_text=None, **kwargs):
        """All brain API calls route through here: times the call and records
        usage/cost telemetry. Telemetry never raises; API errors propagate.

        on_text: optional callable fired with each text delta as it streams
        from the API (used by the voice pipeline to start TTS on the first
        sentence instead of waiting for the full response). When None the
        call is a plain blocking create() — identical to the old behaviour.

        Raises BudgetExceeded, before anything is sent, once the month's API
        budget is spent."""
        from core.telemetry import check_budget
        check_budget()
        started = time.monotonic()
        if on_text is None:
            response = self.client.messages.create(**kwargs)
        else:
            with self.client.messages.stream(**kwargs) as stream:
                for delta in stream.text_stream:
                    on_text(delta)
                response = stream.get_final_message()
        try:
            from core.telemetry import record_api_usage
            record_api_usage(
                _telemetry_source,
                kwargs.get("model", self._model),
                response.usage,
                (time.monotonic() - started) * 1000,
            )
        except Exception:
            pass
        try:
            u = response.usage
            print(f"[El Fager] tokens: in={u.input_tokens} "
                  f"cache_read={getattr(u, 'cache_read_input_tokens', 0) or 0} "
                  f"cache_write={getattr(u, 'cache_creation_input_tokens', 0) or 0} "
                  f"out={u.output_tokens}")
        except Exception:
            pass
        return response

    def _build_system(self, memory_context: str = "", staged=None) -> list:
        """System prompt as content blocks. The static SYSTEM_PROMPT carries a
        cache_control breakpoint (prompt caching: ~0.1x cost + lower latency on
        repeat calls within the TTL); per-turn dynamic context (facts,
        deadlines, memory hits) goes in a second, uncached block so it never
        invalidates the cached prefix."""
        blocks = [{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }]
        # The model was never told the date: in September 2026 it searched and
        # still called 2024's final "the last one" and the iPhone 17 unreleased.
        now = _cairo_now()
        dynamic = (
            f"Right now it is {now:%A, %d %B %Y}, {now:%I:%M %p} in Cairo. What you "
            "know from training may be out of date: for anything recent, current, "
            "latest or priced, trust what your tools return over your own memory."
        )
        # Calling El Fager covers what Mo meant, so "this" has to come from
        # the window he was in before, not the one in front now.
        from core import focus_context
        window = focus_context.describe()
        if window:
            dynamic += (
                f"\n\nBefore Mo turned to you he was in {window}. If he says "
                "\"this\" or \"that\" without saying what, he most likely means "
                "that window."
            )
        if self.memory is not None:
            facts = self.memory.format_facts_for_prompt()
            if facts:
                dynamic += f"\n\n{facts}"
            deadlines = self.memory.get_upcoming_deadlines()
            if deadlines:
                dynamic += f"\n\n{deadlines}"
        from core.career.focus import status_line
        job_hunt = status_line()
        if job_hunt:
            dynamic += f"\n\n{job_hunt}"
        if memory_context:
            dynamic += f"\n\n--- Relevant past context ---\n{memory_context}\n---"
        # History keeps only final text, not tool calls, so without this the
        # model can't tell a draft is already armed and stages it again on
        # every "yes send it".
        if staged is not None:
            what = f"{staged.medium} to {staged.target}"
            if staged.subject:
                what += f", subject \"{staged.subject}\""
            dynamic += (
                f"\n\nSTAGED AND WAITING FOR MO: {what}.\n{staged.body[:300]}\n"
                "It is already drafted and shown to Mo. If he says yes / send it / "
                "go ahead, call confirm_staged_action — do not stage it again. "
                "If he asks for changes, re-stage with them; if the same message "
                "also says yes/send it, call confirm_staged_action right after. "
                "If he cancels, says never mind, or says to remove it, call "
                "cancel_staged_action. Never say it was sent or cancelled "
                "unless one of those tools did it."
            )
        if dynamic:
            blocks.append({"type": "text", "text": dynamic.strip()})
        return blocks

    def chat_background(self, user_message: str, memory_context: str = "") -> str:
        """chat() with a fresh throwaway history. Background callers
        (ProactiveEngine: autonomous tasks, missions, dashboard commands,
        scheduled skills) MUST use this instead of chat(): it keeps their
        turns out of Mo's live voice conversation (the shared history is not
        thread-safe) and stops the history growing unbounded between the
        voice pipeline's resets."""
        return self.chat(user_message, memory_context, history=[])

    def synthesize(self, prompt: str, system: str = "",
                   model: "str | None" = None, max_tokens: int = 400) -> str:
        """One-shot text: no tools, no history, no shared system prompt.

        For callers that already hold the facts and only need them written up
        — the Command Center's briefing prose, which is composed from cards it
        has already fetched. Going through chat() would make the model re-fetch
        the same things over four or five round trips, each one carrying ~8k
        tokens of system prompt and ~4k of tool schemas. This is a single call
        on the fast model. It still routes through _create_message, so it
        lands in telemetry like everything else.
        """
        response = self._create_message(
            "synthesize",
            model=model or self._fast_model,
            max_tokens=max_tokens,
            system=system or "You are El Fager, Mo's assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return next(
            (block.text for block in response.content if block.type == "text"), ""
        )

    def chat(self, user_message: str, memory_context: str = "",
             history: list | None = None, on_text=None) -> str:
        # history=None -> the shared interactive conversation (voice pipeline
        # owns it and resets it at conversation end). Background callers pass
        # their own list via chat_background().
        # on_text -> optional streaming callback, see _create_message.
        hist = history if history is not None else self.conversation_history

        # Log user turn
        if self._logger:
            self._logger.log("user", user_message)

        # "play X" goes straight to Spotify — the two API round trips the tool
        # loop would spend (decide to call play_music, then phrase the reply)
        # add seconds to a request whose answer is already known.
        _play_result = self._try_play_music(user_message)
        if _play_result is not None:
            hist.append({"role": "user", "content": user_message})
            hist.append({"role": "assistant", "content": _play_result})
            if self._logger:
                self._logger.log("assistant", _play_result, ["play_music"])
            return _play_result

        # Only Mo's own conversation acts on what he has staged; background
        # turns neither see it nor confirm it.
        from core import staging
        seen = staging.current() if history is None else None
        system = self._build_system(memory_context, staged=seen)
        turn_model = self._select_model(user_message)

        hist.append({"role": "user", "content": user_message})
        messages = _window_history(hist)

        tools_used: list[str] = []
        last_text = ""
        # Once per turn, not once per iteration: recomputing this inside the
        # loop re-sent a different tools array on every tool round trip and
        # threw away ~20k tokens of cached prefix each time.
        turn_tools = self._tools_for_turn(user_message, hist)

        try:
            for _iteration in range(_MAX_TOOL_ITERATIONS):
                response = self._create_message(
                    "chat",
                    on_text=on_text,
                    model=turn_model,
                    max_tokens=1024,
                    system=system,
                    tools=turn_tools,
                    messages=messages,
                )
                self._offline_mode = False

                if response.stop_reason == "end_turn":
                    text = next(
                        (block.text for block in response.content if block.type == "text"),
                        "",
                    )
                    hist.append({"role": "assistant", "content": text})
                    if self._logger:
                        self._logger.log("assistant", text, tools_used)
                    return text

                elif response.stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": response.content})
                    last_text = next(
                        (block.text for block in response.content if block.type == "text"),
                        last_text,
                    )

                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            tools_used.append(block.name)
                            refused = block.name in _CONFIRM_TOOLS and seen is None
                            background = block.name in _MO_ONLY_TOOLS and history is not None
                            result_str = (
                                _UNSEEN_CONFIRM if refused
                                else _BACKGROUND_REFUSAL if background
                                else self._dispatch_tool(block.name, block.input)
                            )
                            refused = refused or background
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_str,
                                **({"is_error": True} if refused else {}),
                            })

                    messages.append({"role": "user", "content": tool_results})

                else:
                    text = "[Response cut off — please try again]"
                    hist.append({"role": "assistant", "content": text})
                    if self._logger:
                        self._logger.log("assistant", text, tools_used)
                    return text

            text = (
                (last_text + " " if last_text else "")
                + f"[stopped after {_MAX_TOOL_ITERATIONS} steps -- let me know if you want me to continue]"
            )
            hist.append({"role": "assistant", "content": text})
            if self._logger:
                self._logger.log("assistant", text, tools_used)
            return text

        except anthropic.BadRequestError as e:
            msg = str(e)
            if "credit balance" in msg or "billing" in msg.lower() or "Plans & Billing" in msg:
                text = "Your Anthropic API credit balance is too low. Go to console.anthropic.com → Plans & Billing to top up."
            elif "invalid_api_key" in msg or "authentication" in msg.lower():
                text = "Anthropic API key is invalid. Check your ANTHROPIC_API_KEY in .env."
            else:
                text = f"API error: {e}"
            hist.append({"role": "assistant", "content": text})
            if self._logger:
                self._logger.log("assistant", text, tools_used)
            return text

        except anthropic.AuthenticationError:
            text = "Anthropic API key is invalid or expired. Check your ANTHROPIC_API_KEY in .env."
            hist.append({"role": "assistant", "content": text})
            if self._logger:
                self._logger.log("assistant", text, tools_used)
            return text

        except (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            BudgetExceeded,     # the month's budget is spent: the local model until the 1st
        ):
            self._offline_mode = True
            from core.local_llm import local_chat
            try:
                text = local_chat(messages)
            except RuntimeError as err:
                text = str(err)
            hist.append({"role": "assistant", "content": text})
            if self._logger:
                self._logger.log("assistant", text, tools_used)
            return text

    def chat_with_screenshot(self, user_input: str, base64_image: str,
                             memory_context: str = "", on_text=None) -> str:
        from tools.screen_tool import ocr_screenshot

        # Skip the vision API call entirely if we're already known to be offline;
        # go straight to OCR → local LLM to avoid a guaranteed second API failure.
        if self._offline_mode:
            ocr_text = ocr_screenshot()
            return self.chat(
                f"[Screen content via OCR]:\n{ocr_text}\n\nMo asked: {user_input}",
                memory_context,
            )

        from core import staging
        seen = staging.current()
        system = self._build_system(memory_context, staged=seen)

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64_image,
                },
            },
            {
                "type": "text",
                "text": user_input,
            },
        ]

        messages = [{"role": "user", "content": content}]

        if self._logger:
            self._logger.log("user", f"[screenshot] {user_input}")

        tools = _select_tools(user_input)
        tools_used: list[str] = []

        try:
            for _iteration in range(_MAX_TOOL_ITERATIONS):
                response = self._create_message(
                    "screenshot",
                    on_text=on_text,
                    model=self._model,
                    max_tokens=1024,
                    system=system,
                    tools=tools,
                    messages=messages,
                )
                self._offline_mode = False

                if response.stop_reason == "end_turn":
                    text = next(
                        (block.text for block in response.content if block.type == "text"),
                        "",
                    )
                    if self._logger:
                        self._logger.log("assistant", text, tools_used)
                    return text

                elif response.stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": response.content})

                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            tools_used.append(block.name)
                            refused = block.name in _CONFIRM_TOOLS and seen is None
                            result_str = (
                                _UNSEEN_CONFIRM if refused
                                else self._dispatch_tool(block.name, block.input)
                            )
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_str,
                                **({"is_error": True} if refused else {}),
                            })

                    messages.append({"role": "user", "content": tool_results})

                else:
                    return "[Response cut off — please try again]"

            return (
                f"[stopped after {_MAX_TOOL_ITERATIONS} steps -- "
                "let me know if you want me to continue]"
            )

        except anthropic.BadRequestError as e:
            msg = str(e)
            if "credit balance" in msg or "billing" in msg.lower() or "Plans & Billing" in msg:
                return "Your Anthropic API credit balance is too low. Go to console.anthropic.com → Plans & Billing to top up."
            ocr_text = ocr_screenshot()
            return self.chat(
                f"[Screen content via OCR]:\n{ocr_text}\n\nMo asked: {user_input}",
                memory_context,
            )
        except anthropic.AuthenticationError:
            return "Anthropic API key is invalid or expired. Check your ANTHROPIC_API_KEY in .env."
        except (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ):
            self._offline_mode = True
            ocr_text = ocr_screenshot()
            return self.chat(
                f"[Screen content via OCR]:\n{ocr_text}\n\nMo asked: {user_input}",
                memory_context,
            )
        except Exception:
            ocr_text = ocr_screenshot()
            return self.chat(
                f"[Screen content via OCR]:\n{ocr_text}\n\nMo asked: {user_input}",
                memory_context,
            )

    def _tools_for_turn(self, user_message: str, history: list) -> list:
        """The tool list for this turn, stable for as long as it can be.

        _select_tools already folds in recent turns, so the set it returns
        grows as a conversation goes on. Unioning it into what the
        conversation has already sent means the array only ever changes when
        a genuinely new group is triggered — one cache write at that point,
        rather than one per turn and per tool iteration.
        """
        selected = {t["name"] for t in _select_tools(user_message, history)}
        if self._turn_tool_names is None:
            self._turn_tool_names = selected
        else:
            self._turn_tool_names |= selected
        # Switching a skill off in Settings has to take effect now, even for a
        # group this conversation already activated — so disabled names are
        # subtracted from the accumulated set, not just from the new one.
        names = self._turn_tool_names - _disabled_tool_names()
        # Rebuilt from _SLIM_TOOLS rather than kept as objects so the order is
        # always the source order — the cache key is the serialised array, and
        # the same set in a different order is a different prefix.
        return [t for t in _SLIM_TOOLS if t["name"] in names]

    def reset_conversation(self):
        self.conversation_history = []
        self._turn_tool_names = None
