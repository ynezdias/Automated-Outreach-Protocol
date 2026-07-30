/**
 * Compliance tests for the inbound webhook: signature rejection, duplicate
 * delivery, unmatched senders, and lead reply stamping.
 */
@IsTest
private class TwilioInboundRestTest {
    private static final String URI = '/twilio/inbound';
    private static final String PHONE = '+16502530200';

    private static String formBody(String sid, String fromNumber) {
        return 'MessageSid=' + sid +
            '&From=' + EncodingUtil.urlEncode(fromNumber, 'UTF-8') +
            '&To=%2B15005550006&Body=Yes%2C+tell+me+more';
    }

    private static void post(RestRequest request) {
        RestContext.request = request;
        RestContext.response = new RestResponse();
        TwilioInboundRest.handleInbound();
    }

    private static Lead makeLead(String phone) {
        Lead lead = new Lead(
            LastName = 'Prospect',
            Company = 'Acme LLC',
            Phone = phone,
            Outreach_Status__c = 'Contacted'
        );
        insert lead;
        return lead;
    }

    @IsTest
    static void unsignedRequestIsRejected() {
        TwilioWebhookTestUtil.configure();
        post(TwilioWebhookTestUtil.unsignedRequest(URI, formBody('SM_IN_1', PHONE)));
        Assert.areEqual(403, RestContext.response.statusCode, 'unsigned must be rejected');
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }

    @IsTest
    static void invalidSignatureIsRejected() {
        TwilioWebhookTestUtil.configure();
        RestRequest request = TwilioWebhookTestUtil.unsignedRequest(URI, formBody('SM_IN_2', PHONE));
        request.addHeader(
            'X-Twilio-Signature',
            TwilioWebhookTestUtil.sign(URI, formBody('SM_IN_2', PHONE), 'wrong_token_9999')
        );
        post(request);
        Assert.areEqual(403, RestContext.response.statusCode, 'bad signature must be rejected');
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }

    @IsTest
    static void unconfiguredTokenFailsClosed() {
        // no configure(): token and base URL are absent
        post(TwilioWebhookTestUtil.unsignedRequest(URI, formBody('SM_IN_3', PHONE)));
        Assert.areEqual(403, RestContext.response.statusCode, 'unconfigured must reject');
    }

    @IsTest
    static void validInboundLogsMessageAndStampsLead() {
        TwilioWebhookTestUtil.configure();
        Lead lead = makeLead(PHONE);

        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_IN_4', PHONE)));

        Assert.areEqual(200, RestContext.response.statusCode);
        Outreach_Message__c logged = [
            SELECT Lead__c, Direction__c, Status__c, Body__c, Unmatched__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_IN_4'
        ];
        Assert.areEqual(lead.Id, logged.Lead__c);
        Assert.areEqual('Inbound', logged.Direction__c);
        Assert.areEqual('Received', logged.Status__c);
        Assert.isFalse(logged.Unmatched__c);
        Assert.areEqual('Yes, tell me more', logged.Body__c);
        Lead reloaded = [
            SELECT Outreach_Status__c, Last_Reply_At__c FROM Lead WHERE Id = :lead.Id
        ];
        Assert.areEqual('Replied', reloaded.Outreach_Status__c);
        Assert.isNotNull(reloaded.Last_Reply_At__c);
        Assert.isTrue(
            RestContext.response.responseBody.toString().contains('<Response>'),
            'must answer Twilio with empty TwiML'
        );
    }

    @IsTest
    static void duplicateDeliveryCreatesExactlyOneRecord() {
        TwilioWebhookTestUtil.configure();
        makeLead(PHONE);

        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_IN_5', PHONE)));
        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_IN_5', PHONE)));

        Assert.areEqual(200, RestContext.response.statusCode);
        Assert.areEqual(
            1,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Provider_Message_Id__c = 'SM_IN_5'],
            'a provider retry must never double-log'
        );
    }

    @IsTest
    static void unmatchedSenderIsQueuedNotDropped() {
        TwilioWebhookTestUtil.configure();
        // no lead exists for this number

        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_IN_6', '+16505550999')));

        Assert.areEqual(200, RestContext.response.statusCode);
        Outreach_Message__c logged = [
            SELECT Lead__c, Unmatched__c, Body__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_IN_6'
        ];
        Assert.isNull(logged.Lead__c);
        Assert.isTrue(logged.Unmatched__c, 'unmatched senders go to the queue, never dropped');
    }

    @IsTest
    static void nonE164SenderFormatStillMatchesByNormalization() {
        TwilioWebhookTestUtil.configure();
        Lead lead = makeLead('+16502530201');

        post(
            TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_IN_7', '(650) 253-0201'))
        );

        Outreach_Message__c logged = [
            SELECT Lead__c, Unmatched__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_IN_7'
        ];
        Assert.areEqual(lead.Id, logged.Lead__c, 'matching must use normalized phones');
        Assert.isFalse(logged.Unmatched__c);
    }

    @IsTest
    static void missingSidIsRejected() {
        TwilioWebhookTestUtil.configure();
        post(TwilioWebhookTestUtil.signedRequest(URI, 'From=%2B16502530200&Body=hi'));
        Assert.areEqual(400, RestContext.response.statusCode);
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }
}
