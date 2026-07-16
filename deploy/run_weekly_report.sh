#!/bin/bash
# Weekly-report runner (called by launchd every Sunday). Generates the report, saves it,
# and attempts WhatsApp delivery (no-ops gracefully until the Evolution API gateway is live).
# Logs to data/logs/weekly_report.log.
cd "/Users/israelmeir/chatbot/logs/whatsapp_agent" || exit 1
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
echo "===== weekly report run @ $STAMP =====" >> data/logs/weekly_report.log
/usr/bin/python3 -m app.analytics.weekly_report --whatsapp --quiet >> data/logs/weekly_report.log 2>&1
echo "exit=$? @ $(date '+%H:%M:%S')" >> data/logs/weekly_report.log
