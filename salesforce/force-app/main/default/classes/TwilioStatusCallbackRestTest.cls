/**
 * Webhook idempotency tests: providers retry; a duplicate status callback
 * must never create a second Outreach_Message__c record.
 */
@IsTest
private class TwilioStatusCallbackRestTest {
    private static void postCallback(String sid, String status) {
        RestRequest request = new RestRequest();
        request.requestUri = '/services/apexrest/twilio/status';
        request.httpMethod = 'POST';
        request.requestBody = Blob.valueOf(
            'MessageSid=' + sid + '&MessageStatus=' + status + '&To=%2B16502530001'
        );
        RestContext.request = request;
        RestContext.response = new RestResponse();
        TwilioStatusCallbackRest.handleStatus();
    }

    @IsTest
    static void updatesDeliveryStatusOnExistingMessage() {
        insert new Outreach_Message__c(
            Provider_Message_Id__c = 'SM_EXISTING',
            Direction__c = 'Outbound',
            Channel__c = 'SMS',
            Delivery_Status__c = 'queued'
        );

        postCallback('SM_EXISTING', 'delivered');

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
        insert new Outreach_Message__c(
            Provider_Message_Id__c = 'SM_DUP',
            Direction__c = 'Outbound',
            Channel__c = 'SMS',
            Delivery_Status__c = 'queued'
        );

        postCallback('SM_DUP', 'sent');
        postCallback('SM_DUP', 'delivered'); // provider retry / next hop

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
        postCallback('SM_UNKNOWN', 'delivered');
        postCallback('SM_UNKNOWN', 'delivered');

        Assert.areEqual(
            1,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Provider_Message_Id__c = 'SM_UNKNOWN'],
            'unknown-SID callbacks create at most one stub record'
        );
    }

    @IsTest
    static void missingSidIsRejected() {
        postCallback('', 'delivered');
        Assert.areEqual(400, RestContext.response.statusCode);
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }
}
