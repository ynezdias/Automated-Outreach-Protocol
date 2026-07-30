/**
 * TextTorrent implementation of IMessagingProvider.
 *
 * Auth via the TextTorrent Named Credential — no keys in Apex or Custom
 * Settings. All vendor-specific wire details are quarantined HERE and nowhere
 * else.
 *
 * CONTRACT NOTE (ADR-016): docs/PROVIDER.md was not present in the repo when
 * this was written. The wire contract below is pinned by the existing WO-07/
 * WO-08 test suites: sends succeed on 2xx with a JSON body whose message id is
 * under `sid` (with `id`/`message_id` accepted as fallbacks) and provider
 * status under `status`; inbound webhooks are validated as
 * Base64(HMAC-SHA1(signing key, url + sorted params)) in the provider
 * signature header. When PROVIDER.md lands, reconcile THIS class only.
 */
public with sharing class TextTorrentProvider implements IMessagingProvider {
    private static final String SEND_PATH = 'callout:TextTorrent/v1/messages';

    public ProviderResult send(String toNumber, String body, Id leadId) {
        HttpRequest request = new HttpRequest();
        request.setEndpoint(SEND_PATH);
        request.setMethod('POST');
        request.setHeader('Content-Type', 'application/json');
        request.setBody(
            JSON.serialize(
                new Map<String, String>{
                    'to' => toNumber,
                    'from' => OutreachSettings.fromNumber(),
                    'body' => body,
                    'reference' => String.valueOf(leadId)
                }
            )
        );
        HttpResponse response = new Http().send(request);
        if (response.getStatusCode() >= 200 && response.getStatusCode() < 300) {
            Map<String, Object> payload = (Map<String, Object>) JSON.deserializeUntyped(
                response.getBody()
            );
            return new ProviderResult(firstNonNull(payload), (String) payload.get('status'), null);
        }
        return new ProviderResult(
            null,
            null,
            response.getStatusCode() + ' ' + response.getBody()
        );
    }

    public Boolean validateInbound(RestRequest request) {
        return TwilioWebhook.isValidSignature(request);
    }

    private static String firstNonNull(Map<String, Object> payload) {
        for (String key : new List<String>{ 'sid', 'id', 'message_id' }) {
            Object value = payload.get(key);
            if (value != null) {
                return (String) value;
            }
        }
        return null;
    }
}
