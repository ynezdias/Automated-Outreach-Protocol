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
        if (!TwilioWebhook.isValidSignature(request)) {
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
        if (matched != null) {
            matched.Outreach_Status__c = 'Replied';
            matched.Last_Reply_At__c = Datetime.now();
            update matched;
        }
        respondOk(response);
    }

    private static Lead matchLead(String normalizedPhone) {
        if (normalizedPhone == null) {
            return null;
        }
        List<Lead> leads = [
            SELECT Id
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
