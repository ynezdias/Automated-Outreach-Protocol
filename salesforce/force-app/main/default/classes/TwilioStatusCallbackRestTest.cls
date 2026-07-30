/**
 * Webhook idempotency and signature tests for the delivery-status callback.
 */
@IsTest
private class TwilioStatusCallbackRestTest {
    private static final String URI = '/twilio/status';

    private static String formBody(String sid, String status) {
        return 'MessageSid=' + sid + '&MessageStatus=' + status + '&To=%2B16502530001';
    }

    private static void post(RestRequest request) {
        RestContext.request = request;
        RestContext.response = new RestResponse();
        TwilioStatusCallbackRest.handleStatus();
    }

    private static void postSigned(String sid, String status) {
        post(TwilioWebhookTestUtil.signedRequest(URI, formBody(sid, status)));
    }

    @IsTest
    static void unsignedCallbackIsRejected() {
        TwilioWebhookTestUtil.configure();
        post(TwilioWebhookTestUtil.unsignedRequest(URI, formBody('SM_X', 'delivered')));
        Assert.areEqual(403, RestContext.response.statusCode);
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }

    @IsTest
    static void updatesDeliveryStatusOnExistingMessage() {
        TwilioWebhookTestUtil.configure();
        insert new Outreach_Message__c(
            Provider_Message_Id__c = 'SM_EXISTING',
            Direction__c = 'Outbound',
            Channel__c = 'SMS',
            Delivery_Status__c = 'queued'
        );

        postSigned('SM_EXISTING', 'delivered');

        Outreach_Message__c updated = [
            SELECT Delivery_Status__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_EXISTING'
        ];
        Assert.areEqual('delivered', updated.Delivery_Status__c);
        Assert.areEqual(200, RestContext.response.statusCode);
    }

    @IsTest
    static void duplicateCallbackDoesNotCreateSecondRecord() {
        TwilioWebhookTestUtil.configure();
        insert new Outreach_Message__c(
            Provider_Message_Id__c = 'SM_DUP',
            Direction__c = 'Outbound',
            Channel__c = 'SMS',
            Delivery_Status__c = 'queued'
        );

        postSigned('SM_DUP', 'sent');
        postSigned('SM_DUP', 'delivered'); // provider retry / next hop

        Assert.areEqual(
            1,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Provider_Message_Id__c = 'SM_DUP'],
            'a duplicate callback must never create a second record'
        );
        Outreach_Message__c updated = [
            SELECT Delivery_Status__c
            FROM Outreach_Message__c
            WHERE Provider_Message_Id__c = 'SM_DUP'
        ];
        Assert.areEqual('delivered', updated.Delivery_Status__c, 'latest status wins');
    }

    @IsTest
    static void unknownSidCreatesExactlyOneStub() {
        TwilioWebhookTestUtil.configure();
        postSigned('SM_UNKNOWN', 'delivered');
        postSigned('SM_UNKNOWN', 'delivered');

        Assert.areEqual(
            1,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Provider_Message_Id__c = 'SM_UNKNOWN'],
            'unknown-SID callbacks create at most one stub record'
        );
    }

    @IsTest
    static void missingSidIsRejected() {
        TwilioWebhookTestUtil.configure();
        post(TwilioWebhookTestUtil.signedRequest(URI, 'MessageStatus=delivered'));
        Assert.areEqual(400, RestContext.response.statusCode);
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }
}
