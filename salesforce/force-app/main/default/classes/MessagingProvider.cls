/**
 * Factory for the active messaging provider. Production always resolves to
 * TextTorrentProvider. MockMessagingProvider (load tests, ADR-021) requires
 * BOTH the Use_Mock_Provider__c setting AND a sandbox/scratch org — the
 * double gate means production can never run against the mock. Tests may
 * inject a stub via the @TestVisible override.
 */
public with sharing class MessagingProvider {
    @TestVisible
    private static IMessagingProvider instance;
    @TestVisible
    private static Boolean sandboxOverride;

    public static IMessagingProvider get() {
        if (instance == null) {
            instance = OutreachSettings.useMockProvider() && isSandbox()
                ? (IMessagingProvider) new MockMessagingProvider()
                : new TextTorrentProvider();
        }
        return instance;
    }

    private static Boolean isSandbox() {
        if (sandboxOverride != null) {
            return sandboxOverride;
        }
        return [SELECT IsSandbox FROM Organization LIMIT 1].IsSandbox;
    }
}
