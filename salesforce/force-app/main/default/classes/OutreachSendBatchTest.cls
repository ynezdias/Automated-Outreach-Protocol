/**
 * Tests for the hourly send batch (ADR-021): gates hold inside the batch,
 * the scheduler registers hourly, and the mock provider is double-gated.
 */
@IsTest
private class OutreachSendBatchTest {
    private class ProviderHttpMock implements HttpCalloutMock {
        public Integer calls = 0;

        public HttpResponse respond(HttpRequest request) {
            calls++;
            HttpResponse response = new HttpResponse();
            response.setStatusCode(201);
            response.setBody('{"sid": "SM_BATCH_' + calls + '", "status": "queued"}');
            return response;
        }
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

    private static Lead lead(String phone, String timezoneName) {
        return new Lead(
            LastName = 'Prospect ' + phone,
            Company = 'Acme LLC',
            Phone = phone,
            Outreach_Status__c = 'Ready',
            Quiet_Hours_Timezone__c = timezoneName
        );
    }

    @IsTest
    static void batchSendsReadyLeadsWithEveryGateIntact() {
        createApprovedTemplate();
        String inWindow = timezoneWithLocalHour(12);
        List<Lead> leads = new List<Lead>();
        for (Integer i = 0; i < 5; i++) {
            leads.add(lead('+165025320' + String.valueOf(10 + i), inWindow));
        }
        Lead optedOut = lead('+16502530250', inWindow);
        optedOut.Opted_Out__c = true;
        Lead unknownTimezone = lead('+16502530251', 'Not/AZone');
        leads.add(optedOut);
        leads.add(unknownTimezone);
        insert leads;
        ProviderHttpMock mock = new ProviderHttpMock();
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        Database.executeBatch(new OutreachSendBatch(), OutreachSendBatch.SCOPE_SIZE);
        Test.stopTest();

        Assert.areEqual(5, mock.calls, 'exactly the eligible leads produce callouts');
        Assert.areEqual(
            5,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Status__c = 'Sent'],
            'every attempt is logged'
        );
        Assert.areEqual(
            0,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Lead__c = :optedOut.Id],
            'suppression gate holds inside the batch'
        );
        Assert.areEqual(
            0,
            [SELECT COUNT() FROM Outreach_Message__c WHERE Lead__c = :unknownTimezone.Id],
            'unknown timezone fails closed inside the batch'
        );
        Assert.areEqual(
            5,
            [SELECT COUNT() FROM Lead WHERE Outreach_Status__c = 'Contacted'],
            'sent leads advance to Contacted'
        );
    }

    @IsTest
    static void schedulerRegistersTheHourlyJob() {
        Test.startTest();
        OutreachSendScheduler.scheduleHourly();
        Test.stopTest();
        List<CronTrigger> triggers = [
            SELECT CronExpression
            FROM CronTrigger
            WHERE CronJobDetail.Name = :OutreachSendScheduler.JOB_NAME
        ];
        Assert.areEqual(1, triggers.size());
        Assert.areEqual(OutreachSendScheduler.HOURLY_CRON, triggers[0].CronExpression);
    }

    @IsTest
    static void mockProviderRequiresBothSettingAndSandbox() {
        OutreachSettings.overrideRecord = new Outreach_Setting__mdt(Use_Mock_Provider__c = true);
        MessagingProvider.instance = null;
        MessagingProvider.sandboxOverride = true;
        Assert.isInstanceOfType(
            MessagingProvider.get(),
            MockMessagingProvider.class,
            'setting + sandbox -> mock'
        );

        MessagingProvider.instance = null;
        MessagingProvider.sandboxOverride = false;
        Assert.isInstanceOfType(
            MessagingProvider.get(),
            TextTorrentProvider.class,
            'production NEVER resolves the mock, whatever the setting says'
        );

        OutreachSettings.overrideRecord = new Outreach_Setting__mdt(Use_Mock_Provider__c = false);
        MessagingProvider.instance = null;
        MessagingProvider.sandboxOverride = true;
        Assert.isInstanceOfType(
            MessagingProvider.get(),
            TextTorrentProvider.class,
            'sandbox without the setting -> real provider'
        );
        MessagingProvider.instance = null;
        MessagingProvider.sandboxOverride = null;
    }

    @IsTest
    static void mockProviderReturnsUniqueSyntheticSuccess() {
        MockMessagingProvider provider = new MockMessagingProvider();
        ProviderResult first = provider.send('+16502530001', 'body', null);
        ProviderResult second = provider.send('+16502530001', 'body', null);
        Assert.isNull(first.errorCode);
        Assert.isTrue(first.messageId.startsWith('MOCK-'));
        Assert.areNotEqual(first.messageId, second.messageId, 'unique ids under load');
        Assert.isFalse(provider.validateInbound(null), 'the mock never authenticates inbound');
    }
}
