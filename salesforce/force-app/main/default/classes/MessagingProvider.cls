/**
 * Factory for the active messaging provider. Production resolves to
 * TextTorrentProvider; tests may inject a stub via the @TestVisible override.
 */
public with sharing class MessagingProvider {
    @TestVisible
    private static IMessagingProvider instance;

    public static IMessagingProvider get() {
        if (instance == null) {
            instance = new TextTorrentProvider();
        }
        return instance;
    }
}
