/**
 * Compliance tests for the send path. Order of operations under test:
 * suppression -> quiet hours -> rate limit -> send -> log.
 */
@IsTest
private class OutreachSendServiceTest {
    private static final String SENT_SID = 'SM_TEST_0001';

    private class TwilioMock implements HttpCalloutMock {
        public Integer calls = 0;
        public Integer statusCode = 201;

        public HttpResponse respond(HttpRequest request) {
            calls++;
            HttpResponse response = new HttpResponse();
            response.setStatusCode(statusCode);
            response.setBody(
                statusCode == 201
                    ? '{"sid": "' + SENT_SID + '", "status": "queued"}'
                    : '{"message": "boom"}'
            );
            return response;
        }
    }

    private static void configureSettings(Integer hourlyLimit) {
        OutreachSettings.overrideRecord = new Outreach_Setting__mdt(
            Twilio_Account_SID__c = 'ACtest',
            From_Number__c = '+15005550006',
            Per_Number_Hourly_Limit__c = hourlyLimit
        );
    }

    private static void createApprovedTemplate() {
        Reply_Template__c template = new Reply_Template__c(
            Name = 'First Touch',
            Status__c = 'Draft',
            Body__c = 'Hi — quick question about financing for your business.',
            Version__c = 1,
            Intent__c = 'Interested'
        );
        insert template;
        template.Status__c = 'Approved';
        update template;
    }

    /** An Etc/GMT offset zone whose current local hour equals targetHour. */
    private static String timezoneWithLocalHour(Integer targetHour) {
        Integer utcHour = Datetime.now().hourGmt();
        Integer delta = Math.mod(targetHour - utcHour + 24, 24);
        if (delta > 14) {
            delta -= 24;
        }
        if (delta == 0) {
            return 'Etc/GMT';
        }
        // Etc/GMT sign convention is inverted: Etc/GMT-5 means UTC+5.
        return delta > 0 ? 'Etc/GMT-' + delta : 'Etc/GMT+' + (-delta);
    }

    private static Lead makeLead(String phone, String timezoneName) {
        Lead lead = new Lead(
            LastName = 'Prospect',
            Company = 'Acme LLC',
            Phone = phone,
            Outreach_Status__c = 'Ready',
            Quiet_Hours_Timezone__c = timezoneName
        );
        insert lead;
        return lead;
    }

    @IsTest
    static void suppressedLeadIsNeverSentTo() {
        configureSettings(50);
        createApprovedTemplate();
        Lead lead = makeLead('+16502530001', timezoneWithLocalHour(12));
        lead.Opted_Out__c = true;
        // also give it an unknown timezone: suppression must win the ordering
        lead.Quiet_Hours_Timezone__c = 'Not/AZone';
        update lead;
        TwilioMock mock = new TwilioMock();
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ lead.Id }
        );
        Test.stopTest();

        Assert.areEqual('suppressed', outcomes[0].reason, 'suppression must be checked first');
        Assert.isFalse(outcomes[0].sent);
        Assert.areEqual(0, mock.calls, 'no callout may be made for a suppressed lead');
        Assert.areEqual(0, [SELECT COUNT() FROM Outreach_Message__c]);
    }

    @IsTest
    static void outOfHoursLeadIsNotSentTo() {
        configureSettings(50);
        createApprovedTemplate();
        Lead lead = makeLead('+16502530002', timezoneWithLocalHour(3)); // 3am local
        TwilioMock mock = new TwilioMock();
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ lead.Id }
        );
        Test.stopTest();

        Assert.areEqual('quiet_hours', outcomes[0].reason);
        Assert.areEqual(0, mock.calls, 'no callout may be made outside 8am-9pm local');
    }

    @IsTest
    static void unknownTimezoneIsNotSentTo() {
        configureSettings(50);
        createApprovedTemplate();
        Lead invalidZone = makeLead('+16502530003', 'Not/AZone');
        Lead blankZone = makeLead('+16502530004', null);
        TwilioMock mock = new TwilioMock();
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ invalidZone.Id, blankZone.Id }
        );
        Test.stopTest();

        Assert.areEqual('unknown_timezone', outcomes[0].reason, 'invalid tz must fail closed');
        Assert.areEqual('unknown_timezone', outcomes[1].reason, 'blank tz must fail closed');
        Assert.areEqual(0, mock.calls);
    }

    @IsTest
    static void quietHoursCheckedBeforeRateLimit() {
        configureSettings(0); // rate limit would block everything
        createApprovedTemplate();
        Lead lead = makeLead('+16502530005', null);
        Test.setMock(HttpCalloutMock.class, new TwilioMock());

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ lead.Id }
        );
        Test.stopTest();

        Assert.areEqual(
            'unknown_timezone',
            outcomes[0].reason,
            'quiet hours must be evaluated before the rate limit'
        );
    }

    @IsTest
    static void rateLimitThrottlesWithinBatch() {
        configureSettings(1);
        createApprovedTemplate();
        Lead first = makeLead('+16502530006', timezoneWithLocalHour(12));
        Lead second = makeLead('+16502530007', timezoneWithLocalHour(12));
        TwilioMock mock = new TwilioMock();
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ first.Id, second.Id }
        );
        Test.stopTest();

        Assert.isTrue(outcomes[0].sent);
        Assert.areEqual('rate_limited', outcomes[1].reason);
        Assert.areEqual(1, mock.calls, 'only one send may go out under a limit of 1');
    }

    @IsTest
    static void successfulSendIsLoggedWithTwilioSid() {
        configureSettings(50);
        createApprovedTemplate();
        Lead lead = makeLead('+16502530008', timezoneWithLocalHour(12));
        Test.setMock(HttpCalloutMock.class, new TwilioMock());

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ lead.Id }
        );
        Test.stopTest();

        Assert.isTrue(outcomes[0].sent);
        Outreach_Message__c logged = [
            SELECT Provider_Message_Id__c, Status__c, Delivery_Status__c, Direction__c,
                Template_Version__c, Body__c
            FROM Outreach_Message__c
            WHERE Lead__c = :lead.Id
        ];
        Assert.areEqual(SENT_SID, logged.Provider_Message_Id__c, 'the Twilio SID must be logged');
        Assert.areEqual('Sent', logged.Status__c);
        Assert.areEqual('queued', logged.Delivery_Status__c);
        Assert.areEqual(1, logged.Template_Version__c);
        Lead reloaded = [
            SELECT Outreach_Status__c, Last_Contacted_At__c FROM Lead WHERE Id = :lead.Id
        ];
        Assert.areEqual('Contacted', reloaded.Outreach_Status__c);
        Assert.isNotNull(reloaded.Last_Contacted_At__c);
    }

    @IsTest
    static void failedSendIsLoggedNotDropped() {
        configureSettings(50);
        createApprovedTemplate();
        Lead lead = makeLead('+16502530009', timezoneWithLocalHour(12));
        TwilioMock mock = new TwilioMock();
        mock.statusCode = 500;
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ lead.Id }
        );
        Test.stopTest();

        Assert.isFalse(outcomes[0].sent);
        Assert.areEqual('send_failed', outcomes[0].reason);
        Outreach_Message__c logged = [
            SELECT Status__c, Send_Error__c, Provider_Message_Id__c
            FROM Outreach_Message__c
            WHERE Lead__c = :lead.Id
        ];
        Assert.areEqual('Failed', logged.Status__c, 'failures are logged, never dropped');
        Assert.isTrue(logged.Send_Error__c.contains('500'));
        Lead reloaded = [SELECT Outreach_Status__c FROM Lead WHERE Id = :lead.Id];
        Assert.areEqual('Ready', reloaded.Outreach_Status__c, 'a failed send stays Ready');
    }

    @IsTest
    static void noApprovedTemplateBlocksSend() {
        configureSettings(50);
        // no template created
        Lead lead = makeLead('+16502530010', timezoneWithLocalHour(12));
        TwilioMock mock = new TwilioMock();
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<OutreachSendService.SendOutcome> outcomes = OutreachSendService.sendBatch(
            new List<Id>{ lead.Id }
        );
        Test.stopTest();

        Assert.areEqual('no_approved_template', outcomes[0].reason);
        Assert.areEqual(0, mock.calls);
    }

    @IsTest
    static void batchEntrySendsForReadyLead() {
        // The batch runs in its own transactions, so settings come from the
        // deployed Default custom-metadata record, not the test override.
        createApprovedTemplate();
        Lead lead = makeLead('+16502530011', timezoneWithLocalHour(12));
        Test.setMock(HttpCalloutMock.class, new TwilioMock());

        Test.startTest();
        OutreachSendBatch.run();
        Test.stopTest();

        Assert.areEqual(1, [SELECT COUNT() FROM Outreach_Message__c WHERE Lead__c = :lead.Id]);
    }
}
