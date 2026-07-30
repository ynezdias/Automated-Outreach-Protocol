/**
 * Load-test provider (ADR-021): synthetic success, no callout, no vendor, no
 * credits. Selected ONLY when Outreach_Setting__mdt.Use_Mock_Provider__c is
 * true AND the org is a sandbox/scratch org — production always resolves the
 * real provider, so this can never mask real sends. Message ids are unique so
 * Provider_Message_Id__c uniqueness holds under load.
 */
public with sharing class MockMessagingProvider implements IMessagingProvider {
    public ProviderResult send(String toNumber, String body, Id leadId) {
        return new ProviderResult(
            'MOCK-' + leadId + '-' + Crypto.getRandomInteger(),
            'queued',
            null
        );
    }

    public Boolean validateInbound(RestRequest request) {
        return false; // the mock never authenticates inbound traffic
    }
}
