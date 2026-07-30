# Runbook: 5,000-message single-day load test (ADR-021 acceptance)

Runs in a **scratch org** with `MockMessagingProvider` — no callouts, no
credits, no carrier exposure. A real-send variant against the production
TextTorrent account is an explicit owner decision (5,000 real SMS: credits,
carrier reputation, recipient), not something this runbook performs.

## Setup

1. Create/select a scratch org; deploy `force-app`.
2. Setup → Custom Metadata → Outreach Setting → `Default`:
   - `Use_Mock_Provider__c` = **true** (honored only in sandboxes/scratch orgs
     — the factory double-gate ignores it in production).
   - `Per_Number_Hourly_Limit__c` = **400** (5,000/day needs ~385/hr inside a
     13h recipient-local window).
3. Seed 5,000 Ready leads with valid phones and in-window
   `Quiet_Hours_Timezone__c`, PLUS adversarial rows that must all be blocked:
   some with `Opted_Out__c = true`, some `Outreach_Status__c = 'Suppressed'`,
   some with unknown/blank timezones. `sf data import` or anonymous Apex.
4. Register the schedule once: `OutreachSendScheduler.scheduleHourly();`
   (anonymous Apex), or run on demand with `OutreachSendBatch.run();`.

## Verify (all of these, per acceptance)

- Over the day, `SELECT COUNT() FROM Outreach_Message__c WHERE Status__c =
  'Sent'` reaches 5,000; every record carries a `MOCK-` provider id.
- **Suppression on every one**: zero `Outreach_Message__c` rows for any
  opted-out/suppressed lead; their statuses unchanged.
- **Quiet hours on every one**: zero rows for unknown-timezone leads; leads in
  off-window timezones only gain messages once their local window opens.
- Hourly pacing: `SELECT COUNT() FROM Outreach_Message__c WHERE CreatedDate >=
  :lastHour` never exceeds `Per_Number_Hourly_Limit__c`.
- `AsyncApexJob` rows for `OutreachSendBatch` show Completed with no failures.
