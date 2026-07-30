# Runbook: TextTorrent live verification (Conflicts A & B)

Prerequisites: the `outreach/texttorrent` secret exists (created 2026-07-30);
the pipeline and observability stacks are deployed; the paging SNS email
subscription is confirmed (after first deploy, accept the AWS confirmation
mail sent to ydias@fundmatellc.com — until then alarms fire but page no one).

## Conflict A — AI-rewriter canary (ADR-018)

1. Trigger a run (or wait for the daily schedule):
   `aws lambda invoke --function-name <RewriteCanaryFn> --payload '{}' out.json`
2. One-time handset check: compare the SMS received on the test phone
   +1 (551) 235-7742 character-for-character with `submitted` in `out.json` —
   including the line break, the em dash, `''`, `!!`, and the mIxEd casing.
   This is the only step that verifies the carrier path; the daily canary
   verifies the vendor's stored text from then on.
3. Confirm metric `Outreach/TextTorrent CanaryByteIdentical` = 1 for the run
   and `RewriteCanaryAlarm` is OK.
4. Send docs/vendor/ai-rewriter-disable-request.md to Dev@texttorrent.com and
   file the written reply in docs/vendor/.

## Conflict B — opt-out reconciliation acceptance (ADR-019)

1. Pick a test number that is NOT already suppressed on either side (do not
   use the canary number).
2. Opt it out through TextTorrent ONLY — never touching our pipeline: in the
   dashboard add it to the Blocked List (or text an opt-out word to a
   TextTorrent number from that phone).
3. Trigger the job instead of waiting for the nightly run:
   `aws lambda invoke --function-name <SuppressionReconcileFn> --payload '{}' out.json`
4. All three must hold:
   - `out.json` shows the number (E.164) in `added_from_vendor` with
     `divergence` >= 1;
   - our store now suppresses it: a fresh `SuppressionStore.is_suppressed`
     returns True with source `texttorrent_reconciliation`;
   - `OptOutDivergenceAlarm` enters ALARM and the paging email arrives.
5. Expected on the FIRST production run: the job imports the entire existing
   vendor blocked list (~10.5k numbers) into our store and alarms once. That
   is correct behavior, not a failure (ADR-019).
