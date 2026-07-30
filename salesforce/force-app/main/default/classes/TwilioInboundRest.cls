/**
 * Twilio inbound-message webhook (exposed on a Salesforce Site — see
 * docs/RUNBOOK_inbound.md for site setup).
 *
 * Every request must carry a valid X-Twilio-Signature; unsigned or invalid
 * requests are rejected 403. Inserts are idempotent on
 * Provider_Message_Id__c: provider retries can never double-log (double
 * logging causes double replies). Senders with no matching Lead are logged to
 * the unmatched queue (Unmatched__c = true) rather than dropped.
 *
 * Order of operations is fixed (ADR-020): signature check -> idempotent
 * insert -> guardrails -> persist result. Guardrails run after the insert so
 * a provider retry short-circuits at the idempotency check and can never
 * re-run suppression side effects. On suppress_and_stop the Lead opt-out is
 * written synchronously in this transaction (it gates the next send) and the
 * AWS suppression list is mirrored async via OutreachSuppressionSyncQueueable.
 * On human_review the message is flagged; no automated response of any kind.
 *
 * Deliberately does NOT call the classifier: replies are answered manually
 * for now, which generates labeled training data.
 */
@RestResource(urlMapping='/twilio/inbound')
global with sharing class TwilioInboundRest {
    private static final String EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>';

    @HttpPost
    global static void handleInbound() {
        RestRequest request = RestContext.request;
        RestResponse response = RestContext.response;
        if (!MessagingProvider.get().validateInbound(request)) {
            response.statusCode = 403;
            return;
        }
        Map<String, String> form = TwilioWebhook.parseForm(
            request.requestBody == null ? '' : request.requestBody.toString()
        );
        String sid = form.get('MessageSid');
        String fromNumber = form.get('From');
        if (String.isBlank(sid)) {
            response.statusCode = 400;
            return;
        }
        // Idempotency: a retry of an already-logged message is a no-op.
        if (![SELECT Id FROM Outreach_Message__c WHERE Provider_Message_Id__c = :sid LIMIT 1]
            .isEmpty()) {
            respondOk(response);
            return;
        }
        Lead matched = matchLead(TwilioWebhook.normalizePhone(fromNumber));
        Outreach_Message__c message = new Outreach_Message__c(
            Provider_Message_Id__c = sid,
            Direction__c = 'Inbound',
            Channel__c = 'SMS',
            Status__c = 'Received',
            Delivery_Status__c = 'received',
            Body__c = form.get('Body'),
            Sent_At__c = Datetime.now(),
            Lead__c = matched == null ? null : matched.Id,
            Unmatched__c = matched == null
        );
        try {
            insert message;
        } catch (DmlException raceLoser) {
            // A concurrent provider retry won the insert; nothing more to do.
            respondOk(response);
            return;
        }

        // Guardrails: deterministic, no network, evaluated exactly once per
        // provider message id (the idempotent insert above is the gate).
        Integer priorAutoReplies = matched == null || matched.Auto_Reply_Count__c == null
            ? 0
            : Integer.valueOf(matched.Auto_Reply_Count__c);
        OutreachGuardrails.Result verdict = OutreachGuardrails.evaluate(
            form.get('Body'),
            priorAutoReplies,
            'sms'
        );
        Boolean suppress = verdict.action == OutreachGuardrails.SUPPRESS_AND_STOP;
        String normalizedFrom = TwilioWebhook.normalizePhone(fromNumber);
        message.Guardrail_Action__c = verdict.action;
        message.Guardrail_Rule__c = verdict.rule;
        if (verdict.action == OutreachGuardrails.HUMAN_REVIEW) {
            message.Needs_Review__c = true;
        }
        if (suppress && normalizedFrom == null) {
            // No identifier to mirror to the AWS list — a human must close it.
            message.Needs_Review__c = true;
        }
        update message;

        if (matched != null) {
            matched.Outreach_Status__c = suppress ? 'Suppressed' : 'Replied';
            matched.Last_Reply_At__c = Datetime.now();
            if (suppress) {
                matched.Opted_Out__c = true;
                matched.DoNotCall = true;
                matched.HasOptedOutOfEmail = true;
                matched.Opt_Out_At__c = Datetime.now();
                matched.Opt_Out_Source__c = 'inbound_sms_guardrail';
            }
            update matched;
        }
        if (suppress && normalizedFrom != null) {
            System.enqueueJob(
                new OutreachSuppressionSyncQueueable(normalizedFrom, verdict.rule, message.Id, 0)
            );
        }
        respondOk(response);
    }

    private static Lead matchLead(String normalizedPhone) {
        if (normalizedPhone == null) {
            return null;
        }
        List<Lead> leads = [
            SELECT Id, Auto_Reply_Count__c
            FROM Lead
            WHERE Phone = :normalizedPhone
            ORDER BY CreatedDate DESC
            LIMIT 1
        ];
        return leads.isEmpty() ? null : leads[0];
    }

    private static void respondOk(RestResponse response) {
        response.statusCode = 200;
        response.addHeader('Content-Type', 'application/xml');
        response.responseBody = Blob.valueOf(EMPTY_TWIML);
    }
}
