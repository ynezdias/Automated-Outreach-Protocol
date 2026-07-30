/**
 * Twilio delivery-status webhook. Idempotent on Provider_Message_Id__c:
 * callbacks update the existing message record; a callback for an unknown SID
 * creates exactly one stub (the unique external ID field breaks ties under
 * concurrent retries), so a duplicate callback can never create a second
 * record or trigger any reply logic.
 *
 * NOTE: Twilio X-Twilio-Signature validation is a pre-production follow-up
 * (ADR-013) — the auth token lives only in the Named Credential and is not
 * readable by Apex for HMAC computation.
 */
@RestResource(urlMapping='/twilio/status')
global with sharing class TwilioStatusCallbackRest {
    @HttpPost
    global static void handleStatus() {
        RestRequest request = RestContext.request;
        RestResponse response = RestContext.response;
        Map<String, String> form = parseForm(
            request.requestBody == null ? '' : request.requestBody.toString()
        );
        String sid = form.get('MessageSid');
        String status = form.get('MessageStatus');
        if (String.isBlank(sid) || String.isBlank(status)) {
            response.statusCode = 400;
            return;
        }
        List<Outreach_Message__c> existing = [
            SELECT Id, Delivery_Status__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = :sid
            LIMIT 1
        ];
        if (!existing.isEmpty()) {
            existing[0].Delivery_Status__c = status;
            update existing[0];
        } else {
            try {
                insert new Outreach_Message__c(
                    Provider_Message_Id__c = sid,
                    Direction__c = 'Outbound',
                    Channel__c = 'SMS',
                    Delivery_Status__c = status,
                    Send_Error__c = 'status callback for unknown SID'
                );
            } catch (DmlException raceLoser) {
                // A concurrent retry inserted it first — update that record instead.
                List<Outreach_Message__c> winner = [
                    SELECT Id
                    FROM Outreach_Message__c
                    WHERE Provider_Message_Id__c = :sid
                    LIMIT 1
                ];
                if (!winner.isEmpty()) {
                    winner[0].Delivery_Status__c = status;
                    update winner[0];
                }
            }
        }
        response.statusCode = 200;
    }

    private static Map<String, String> parseForm(String body) {
        Map<String, String> values = new Map<String, String>();
        for (String pair : body.split('&')) {
            List<String> parts = pair.split('=', 2);
            if (parts.size() == 2) {
                values.put(
                    EncodingUtil.urlDecode(parts[0], 'UTF-8'),
                    EncodingUtil.urlDecode(parts[1], 'UTF-8')
                );
            }
        }
        return values;
    }
}
