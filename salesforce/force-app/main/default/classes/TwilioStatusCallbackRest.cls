/**
 * Twilio delivery-status webhook. Every request must carry a valid
 * X-Twilio-Signature (unsigned or invalid -> 403; fail closed when
 * unconfigured). Idempotent on Provider_Message_Id__c: callbacks update the
 * existing message record; a callback for an unknown SID creates exactly one
 * stub (the unique external ID field breaks ties under concurrent retries),
 * so a duplicate callback can never create a second record or trigger any
 * reply logic.
 */
@RestResource(urlMapping='/twilio/status')
global with sharing class TwilioStatusCallbackRest {
    @HttpPost
    global static void handleStatus() {
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
}
