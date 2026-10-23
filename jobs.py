"""Scheduled work. SAS has no automatic e-mail: guardian notices are sent manually from the records list
(decision of 2026-10). Jobs registered here with scheduler.register_job run hourly between 06:00 and 23:00
for each organization inside its own tenant context."""
