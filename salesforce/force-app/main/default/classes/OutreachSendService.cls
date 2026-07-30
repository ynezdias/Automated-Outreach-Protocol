/**
 * The send path. Per-lead order of operations is NOT negotiable:
 *
 *   1. suppression check  2. quiet hours check  3. rate limit  4. send  5. log
 *
 * Apex forbids callouts after DML in one transaction, so all callouts run
 * first and ALL logging DML happens at the end — the per-lead decision order
 * above is still enforced exactly, and every attempt (success or failure) is
 * logged to Outreach_Message__c. Blocked leads are never sent and their block
 * reason is returned to the caller.
 *
 * Quiet hours: 8am-9pm in the recipient's timezone (Quiet_Hours_Timezone__c).
 * Unknown or invalid timezone -> do NOT send (fail closed).
 */
public with sharing class OutreachSendService {
    public static final Integer QUIET_HOURS_START = 8; // inclusive, local
    public static final Integer QUIET_HOURS_END = 21; // exclusive, local
    @TestVisible
    private static final Integer MAX_CALLOUTS_PER_RUN = 90; // platform limit is 100

    public class SendOutcome {
        public Id leadId;
        public Boolean sent = false;
        public String reason;

        public SendOutcome(Id leadId) {
            this.leadId = leadId;
        }
    }

    public static List<SendOutcome> sendBatch(List<Id> leadIds) {
        List<Lead> leads = [
            SELECT Id, Phone, Opted_Out__c, Outreach_Status__c, Quiet_Hours_Timezone__c
            FROM Lead
            WHERE Id IN :leadIds
            ORDER BY Id
        ];
        Reply_Template__c template = approvedFirstTouchTemplate();
        Integer hourlyLimit = OutreachSettings.perNumberHourlyLimit();
        Integer sentThisHour = [
            SELECT COUNT()
            FROM Outreach_Message__c
            WHERE Direction__c = 'Outbound' AND CreatedDate >= :Datetime.now().addHours(-1)
        ];

        List<SendOutcome> outcomes = new List<SendOutcome>();
        List<Outreach_Message__c> attemptLog = new List<Outreach_Message__c>();
        List<Lead> contactedLeads = new List<Lead>();
        Integer calloutsMade = 0;

        for (Lead lead : leads) {
            SendOutcome outcome = new SendOutcome(lead.Id);
            outcomes.add(outcome);

            // 1. Suppression — always first; a suppressed contact is never sent to.
            if (lead.Opted_Out__c == true || lead.Outreach_Status__c == 'Suppressed') {
                outcome.reason = 'suppressed';
                continue;
            }
            if (lead.Outreach_Status__c != 'Ready') {
                outcome.reason = 'not_ready';
                continue;
            }
            // 2. Quiet hours — unknown timezone fails closed.
            String quietHoursBlock = quietHoursBlockReason(lead.Quiet_Hours_Timezone__c);
            if (quietHoursBlock != null) {
                outcome.reason = quietHoursBlock;
                continue;
            }
            // 3. Rate limit — counts prior hour plus sends already made in this run.
            if (sentThisHour + calloutsMade >= hourlyLimit) {
                outcome.reason = 'rate_limited';
                continue;
            }
            // 4. Send.
            if (template == null) {
                outcome.reason = 'no_approved_template';
                continue;
            }
            if (String.isBlank(lead.Phone)) {
                outcome.reason = 'missing_phone';
                continue;
            }
            if (calloutsMade >= MAX_CALLOUTS_PER_RUN) {
                outcome.reason = 'deferred';
                continue;
            }
            calloutsMade++;
            Outreach_Message__c attempt = performSend(lead, template);
            // 5. Log — every attempt, success or failure, lands in Outreach_Message__c.
            attemptLog.add(attempt);
            if (attempt.Status__c == 'Sent') {
                outcome.sent = true;
                contactedLeads.add(
                    new Lead(
                        Id = lead.Id,
                        Outreach_Status__c = 'Contacted',
                        Last_Contacted_At__c = Datetime.now()
                    )
                );
            } else {
                outcome.reason = 'send_failed';
            }
        }

        insert attemptLog;
        update contactedLeads;
        return outcomes;
    }

    /** Returns null when sending is allowed, else the block reason. */
    @TestVisible
    private static String quietHoursBlockReason(String timezoneName) {
        if (String.isBlank(timezoneName)) {
            return 'unknown_timezone';
        }
        TimeZone recipientZone = TimeZone.getTimeZone(timezoneName);
        // Apex silently returns GMT for unrecognized ids — that must fail closed.
        if (recipientZone.getID() != timezoneName) {
            return 'unknown_timezone';
        }
        Datetime nowUtc = Datetime.now();
        Integer offsetMillis = recipientZone.getOffset(nowUtc);
        Integer localHour = Datetime.newInstance(nowUtc.getTime() + offsetMillis).hourGmt();
        if (localHour < QUIET_HOURS_START || localHour >= QUIET_HOURS_END) {
            return 'quiet_hours';
        }
        return null;
    }

    private static Outreach_Message__c performSend(Lead lead, Reply_Template__c template) {
        Outreach_Message__c attempt = new Outreach_Message__c(
            Lead__c = lead.Id,
            Direction__c = 'Outbound',
            Channel__c = 'SMS',
            Body__c = template.Body__c,
            Reply_Template__c = template.Id,
            Template_Version__c = template.Version__c,
            Sent_At__c = Datetime.now()
        );
        HttpRequest request = new HttpRequest();
        request.setEndpoint(
            'callout:Twilio/2010-04-01/Accounts/' +
                OutreachSettings.twilioAccountSid() +
                '/Messages.json'
        );
        request.setMethod('POST');
        request.setHeader('Content-Type', 'application/x-www-form-urlencoded');
        request.setBody(
            'To=' + EncodingUtil.urlEncode(lead.Phone, 'UTF-8') +
                '&From=' + EncodingUtil.urlEncode(OutreachSettings.fromNumber(), 'UTF-8') +
                '&Body=' + EncodingUtil.urlEncode(template.Body__c, 'UTF-8')
        );
        try {
            HttpResponse response = new Http().send(request);
            if (response.getStatusCode() == 201) {
                Map<String, Object> payload = (Map<String, Object>) JSON.deserializeUntyped(
                    response.getBody()
                );
                attempt.Provider_Message_Id__c = (String) payload.get('sid');
                attempt.Delivery_Status__c = (String) payload.get('status');
                attempt.Status__c = 'Sent';
            } else {
                attempt.Status__c = 'Failed';
                attempt.Delivery_Status__c = 'failed';
                attempt.Send_Error__c = truncate(
                    response.getStatusCode() + ' ' + response.getBody()
                );
            }
        } catch (Exception error) {
            attempt.Status__c = 'Failed';
            attempt.Delivery_Status__c = 'failed';
            attempt.Send_Error__c = truncate(error.getMessage());
        }
        return attempt;
    }

    private static Reply_Template__c approvedFirstTouchTemplate() {
        List<Reply_Template__c> templates = [
            SELECT Id, Body__c, Version__c
            FROM Reply_Template__c
            WHERE Name = 'First Touch' AND Status__c = 'Approved'
            ORDER BY Version__c DESC
            LIMIT 1
        ];
        return templates.isEmpty() ? null : templates[0];
    }

    private static String truncate(String value) {
        return value == null ? null : value.abbreviate(255);
    }
}
