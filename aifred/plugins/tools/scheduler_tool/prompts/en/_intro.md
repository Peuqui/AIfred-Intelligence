SCHEDULER TOOLS:
You can create, list, and delete scheduled tasks for the user.

When the user plans something ("remind me", "every morning", "in 2 hours"):
1. Use scheduler_create with the appropriate schedule_type:
   - "every day at 8" → cron "0 8 * * *"
   - "every 30 minutes" → interval "1800"
   - "tomorrow at 10" → once "YYYY-MM-DDT10:00:00" (calculate the actual date!)
   - "weekdays at 9" → cron "0 9 * * 1-5"
   - "every Monday at 9" → cron "0 9 * * 1"
2. Choose the right delivery mode:
   - User wants result via Telegram/Discord/Email → delivery="announce", channel="telegram"
   - User just wants a reminder → delivery="review" (shows in UI)
   - User says nothing specific → delivery="review" (toast in UI, default)
3. For delivery="announce": Use recipient with the USER NAME (e.g. "Lord Helmchen"), NOT an ID. The scheduler automatically resolves the name to the correct channel ID. If the user doesn't specify a recipient, omit it — the scheduler sends to the primary user.
4. Formulate the message as a PLAIN TEXT instruction (NO code, NO Python, NO variables!). The message is a prompt to the LLM at execution time.
5. The message should describe ONLY the task, NOT the delivery. WRONG: "Generate a prayer and send it via Telegram". RIGHT: "Generate a short prayer." The scheduler handles delivery to the correct channel automatically.
6. The LLM executing the job must NOT call telegram_send/discord_send/email itself. It only generates the response — the scheduler delivers it.

IMPORTANT: When the user asks for system status, call system_status DIRECTLY — do NOT create a scheduler job for it!

TYPICAL USE CASES (suggest these proactively when appropriate):

Email summary:
  scheduler_create(name="email_summary", schedule_type="cron", schedule_expr="0 7 * * *",
    message="Check my recent unread emails using the email tool. Summarize the key points briefly.",
    delivery="announce", channel="telegram", recipient="Lord Helmchen")

Calendar reminder:
  scheduler_create(name="calendar_morning", schedule_type="cron", schedule_expr="0 8 * * 1-5",
    message="Check my appointments for today using epim_search. List them chronologically.",
    delivery="announce", channel="telegram")

One-time reminder:
  scheduler_create(name="doctor_reminder", schedule_type="once", schedule_expr="2026-03-30T09:30:00",
    message="Reminder: Doctor's appointment at 10:00!",
    delivery="announce", channel="telegram")

Periodic status check:
  scheduler_create(name="server_health", schedule_type="interval", schedule_expr="3600",
    message="Run a quick system status check. Report only problems.",
    delivery="review")

When the user asks "what did I schedule" or "show my jobs" → scheduler_list
When the user says "delete job X" or "stop the reminder" → scheduler_delete
