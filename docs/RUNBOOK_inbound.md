# Runbook: Inbound Webhook Setup and Verification

The inbound path is code-complete and unit-tested; exposing it publicly
requires org-specific setup that cannot ship as metadata (Site domains and
guest profiles are org-bound). Follow this once per org.

## 1. Create the Site

1. Setup → Sites → register the Force.com domain if not already done.
2. New Site: label `Outreach Webhooks`, active site home page can be any
   placeholder (the site exists only to expose Apex REST).
3. Note the site URL, e.g. `https://<domain>.my.salesforce-sites.com`.

## 2. Grant the guest user access

On the site's **Public Access Settings** (the auto-created guest profile):

- Enabled Apex Class Access: `TwilioInboundRest`, `TwilioStatusCallbackRest`,
  `TwilioWebhook`, `OutreachSettings`.
- Object permissions: Outreach_Message__c — Read, Create, Edit;
  Lead — Read, Edit.
- Field permissions: all `Outreach_Message__c` fields used by the webhooks
  (Provider_Message_Id__c, Direction__c, Channel__c, Status__c,
  Delivery_Status__c, Body__c, Sent_At__c, Unmatched__c, Send_Error__c) and
  `Lead.Outreach_Status__c`, `Lead.Last_Reply_At__c`, `Lead.Phone`.

## 3. Configure signature validation (required — endpoints fail closed)

1. Setup → Custom Metadata Types → Outreach Secret → Manage Records →
   `Default` → set **Twilio Auth Token** to the account's auth token.
2. Outreach Setting → `Default` → set **Webhook Base URL** to
   `https://<domain>.my.salesforce-sites.com/services/apexrest`
   (exactly what precedes `/twilio/...` in the URLs Twilio calls — Twilio
   signs the full URL, so this must match character for character).

Until both values are set, every webhook request is rejected with 403.

## 4. Point Twilio at the endpoints

In the Twilio console, on the messaging number / Messaging Service:

- Inbound webhook: `<base>/twilio/inbound` (HTTP POST)
- Status callback: `<base>/twilio/status` (HTTP POST)

## 5. Live verification (acceptance)

From a test phone that exists as a Lead (Phone in E.164):

1. Text the Twilio number.
2. Confirm exactly one record:
   `SELECT COUNT() FROM Outreach_Message__c WHERE Direction__c = 'Inbound'
   AND Provider_Message_Id__c != null` → expect 1, and the Lead shows
   `Outreach_Status__c = 'Replied'` with `Last_Reply_At__c` stamped.
3. Replay the same webhook (Twilio console → Messaging → request inspector →
   resend, or curl with the same MessageSid and a valid signature): count
   stays 1.
4. Text from a phone with no Lead: one record with `Unmatched__c = true`.
5. Curl the endpoint without an `X-Twilio-Signature` header: expect 403 and
   no new record.

Replies are answered manually from the Outreach Message record for now — the
classifier is intentionally not wired in yet; manual answers become training
data.
