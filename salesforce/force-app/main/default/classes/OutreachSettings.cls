/**
 * Reads Outreach_Setting__mdt. Secrets never live here: the Twilio auth token
 * exists only in the Twilio Named Credential.
 */
public with sharing class OutreachSettings {
    // Default stays well under starter 10DLC daily tiers even running 24h.
    public static final Integer DEFAULT_PER_NUMBER_HOURLY_LIMIT = 50;

    @TestVisible
    private static Outreach_Setting__mdt overrideRecord;

    private static Outreach_Setting__mdt record() {
        if (overrideRecord != null) {
            return overrideRecord;
        }
        return Outreach_Setting__mdt.getInstance('Default');
    }

    public static Integer perNumberHourlyLimit() {
        Outreach_Setting__mdt rec = record();
        if (rec == null || rec.Per_Number_Hourly_Limit__c == null) {
            return DEFAULT_PER_NUMBER_HOURLY_LIMIT;
        }
        return Integer.valueOf(rec.Per_Number_Hourly_Limit__c);
    }

    public static String twilioAccountSid() {
        Outreach_Setting__mdt rec = record();
        return rec == null || rec.Twilio_Account_SID__c == null ? '' : rec.Twilio_Account_SID__c;
    }

    public static String fromNumber() {
        Outreach_Setting__mdt rec = record();
        return rec == null || rec.From_Number__c == null ? '' : rec.From_Number__c;
    }

    public static String webhookBaseUrl() {
        Outreach_Setting__mdt rec = record();
        return rec == null || rec.Webhook_Base_Url__c == null ? '' : rec.Webhook_Base_Url__c;
    }

    /** Load-test switch (ADR-021); only honored in sandboxes — see MessagingProvider. */
    public static Boolean useMockProvider() {
        Outreach_Setting__mdt rec = record();
        return rec != null && rec.Use_Mock_Provider__c == true;
    }
}
