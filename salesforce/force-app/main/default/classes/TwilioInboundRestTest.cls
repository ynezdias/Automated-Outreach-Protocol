/**
 * Compliance tests for the inbound webhook: signature rejection, duplicate
 * delivery, unmatched senders, and lead reply stamping.
 */
@IsTest
private class TwilioInboundRestTest {
    private static final String URI = '/twilio/inbound';
    private static final String PHONE = '+16502530200';

    private static String formBody(String sid, String fromNumber) {
        return formBody(sid, fromNumber, 'Yes, tell me more');
    }

    private static String formBody(String sid, String fromNumber, String body) {
        return 'MessageSid=' + sid +
            '&From=' + EncodingUtil.urlEncode(fromNumber, 'UTF-8') +
            '&To=%2B15005550006&Body=' + EncodingUtil.urlEncode(body, 'UTF-8');
    }

    private class AwsCalloutMock implements HttpCalloutMock {
        Integer statusCode;
        Integer calls = 0;
        String lastBody;
        String lastEndpoint;

        AwsCalloutMock(Integer statusCode) {
            this.statusCode = statusCode;
        }

        public HttpResponse respond(HttpRequest request) {
            calls++;
            lastBody = request.getBody();
            lastEndpoint = request.getEndpoint();
            HttpResponse response = new HttpResponse();
            response.setStatusCode(statusCode);
            response.setBody('{"ok":true}');
            return response;
        }
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

    // --- guardrail wiring (ADR-020) --------------------------------------------

    @IsTest
    static void inboundStopSuppressesSynchronouslyAndMirrorsToAws() {
        TwilioWebhookTestUtil.configure();
        Lead lead = makeLead(PHONE);
        AwsCalloutMock mock = new AwsCalloutMock(200);
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_STOP_1', PHONE, 'STOP')));
        // The Salesforce opt-out is committed BEFORE the async AWS mirror runs:
        // Salesforce gates the next send, so it can never wait on a callout.
        Lead midFlight = [
            SELECT Opted_Out__c, Outreach_Status__c FROM Lead WHERE Id = :lead.Id
        ];
        Assert.isTrue(midFlight.Opted_Out__c, 'Salesforce suppression must be synchronous');
        Assert.areEqual('Suppressed', midFlight.Outreach_Status__c);
        Test.stopTest();

        Outreach_Message__c logged = [
            SELECT Guardrail_Action__c, Guardrail_Rule__c, Needs_Review__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_STOP_1'
        ];
        Assert.areEqual('suppress_and_stop', logged.Guardrail_Action__c);
        Assert.areEqual('opt_out', logged.Guardrail_Rule__c);
        Assert.isFalse(logged.Needs_Review__c);
        Lead reloaded = [
            SELECT DoNotCall, HasOptedOutOfEmail, Opt_Out_At__c, Opt_Out_Source__c
            FROM Lead
            WHERE Id = :lead.Id
        ];
        Assert.isTrue(reloaded.DoNotCall);
        Assert.isTrue(reloaded.HasOptedOutOfEmail, 'opt-out is cross-channel');
        Assert.isNotNull(reloaded.Opt_Out_At__c);
        Assert.areEqual('inbound_sms_guardrail', reloaded.Opt_Out_Source__c);
        Assert.areEqual(1, mock.calls, 'exactly one AWS mirror callout');
        Assert.isTrue(
            mock.lastEndpoint.contains('AWS_Suppression'),
            'mirror must go through the Named Credential'
        );
        Assert.isTrue(mock.lastBody.contains(PHONE), 'identifier must be in the payload');
        Assert.isTrue(mock.lastBody.contains('salesforce_inbound_guardrail'));
    }

    @IsTest
    static void legalThreatIsFlaggedForReviewNeverSuppressed() {
        TwilioWebhookTestUtil.configure();
        Lead lead = makeLead(PHONE);
        AwsCalloutMock mock = new AwsCalloutMock(200);
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        post(TwilioWebhookTestUtil.signedRequest(
            URI, formBody('SM_LEGAL_1', PHONE, 'I will have my attorney call you')
        ));
        Test.stopTest();

        Outreach_Message__c logged = [
            SELECT Guardrail_Action__c, Guardrail_Rule__c, Needs_Review__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_LEGAL_1'
        ];
        Assert.areEqual('human_review', logged.Guardrail_Action__c);
        Assert.areEqual('legal_escalation', logged.Guardrail_Rule__c);
        Assert.isTrue(logged.Needs_Review__c, 'legal threats go to the reviewer queue');
        Lead reloaded = [
            SELECT Opted_Out__c, Outreach_Status__c FROM Lead WHERE Id = :lead.Id
        ];
        Assert.isFalse(reloaded.Opted_Out__c, 'human_review never auto-suppresses');
        Assert.areEqual('Replied', reloaded.Outreach_Status__c);
        Assert.areEqual(0, mock.calls, 'no AWS mirror for human_review');
    }

    @IsTest
    static void cleanReplyRecordsProceedVerdict() {
        TwilioWebhookTestUtil.configure();
        makeLead(PHONE);

        post(TwilioWebhookTestUtil.signedRequest(
            URI, formBody('SM_CLEAN_1', PHONE, 'interested, send the details')
        ));

        Outreach_Message__c logged = [
            SELECT Guardrail_Action__c, Guardrail_Rule__c, Needs_Review__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_CLEAN_1'
        ];
        Assert.areEqual('proceed', logged.Guardrail_Action__c);
        Assert.isNull(logged.Guardrail_Rule__c);
        Assert.isFalse(logged.Needs_Review__c);
    }

    @IsTest
    static void duplicateStopDeliveryMirrorsExactlyOnce() {
        TwilioWebhookTestUtil.configure();
        makeLead(PHONE);
        AwsCalloutMock mock = new AwsCalloutMock(200);
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_STOP_2', PHONE, 'STOP')));
        post(TwilioWebhookTestUtil.signedRequest(URI, formBody('SM_STOP_2', PHONE, 'STOP')));
        Test.stopTest();

        Assert.areEqual(
            1,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Provider_Message_Id__c = 'SM_STOP_2']
        );
        Assert.areEqual(1, mock.calls, 'a provider retry must never double-suppress');
    }

    @IsTest
    static void unmatchedStopStillMirrorsToAws() {
        TwilioWebhookTestUtil.configure();
        AwsCalloutMock mock = new AwsCalloutMock(200);
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        post(TwilioWebhookTestUtil.signedRequest(
            URI, formBody('SM_STOP_3', '+16505550777', 'unsubscribe')
        ));
        Test.stopTest();

        Outreach_Message__c logged = [
            SELECT Unmatched__c, Guardrail_Action__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_STOP_3'
        ];
        Assert.isTrue(logged.Unmatched__c);
        Assert.areEqual('suppress_and_stop', logged.Guardrail_Action__c);
        Assert.areEqual(1, mock.calls, 'no Lead is needed to honor an opt-out');
        Assert.isTrue(mock.lastBody.contains('+16505550777'));
    }

    @IsTest
    static void failedAwsMirrorFlagsMessageForReviewAfterFinalAttempt() {
        TwilioWebhookTestUtil.configure();
        Outreach_Message__c message = new Outreach_Message__c(
            Provider_Message_Id__c = 'SM_STOP_4',
            Direction__c = 'Inbound',
            Channel__c = 'SMS',
            Body__c = 'STOP'
        );
        insert message;
        Test.setMock(HttpCalloutMock.class, new AwsCalloutMock(500));

        Test.startTest();
        System.enqueueJob(new OutreachSuppressionSyncQueueable(
            PHONE, 'opt_out', message.Id, OutreachSuppressionSyncQueueable.MAX_ATTEMPTS - 1
        ));
        Test.stopTest();

        Outreach_Message__c reloaded = [
            SELECT Needs_Review__c, Send_Error__c FROM Outreach_Message__c WHERE Id = :message.Id
        ];
        Assert.isTrue(reloaded.Needs_Review__c, 'terminal sync failure must reach a human');
        Assert.isTrue(reloaded.Send_Error__c.contains('suppression sync failed'));
    }
}
