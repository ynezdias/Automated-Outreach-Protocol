/**
 * Shared Twilio webhook plumbing: X-Twilio-Signature validation (fail closed),
 * form parsing, and phone normalization mirroring the cleansing pipeline's
 * US-region E.164 rules.
 *
 * Signature scheme: Base64(HMAC-SHA1(authToken, url + concat(sorted POST
 * params as name+value))). The URL is reconstructed from
 * Outreach_Setting__mdt.Webhook_Base_Url__c + the request URI, so the setting
 * must match the URL configured in the Twilio console exactly.
 */
public with sharing class TwilioWebhook {
    @TestVisible
    private static String authTokenOverride;

    public static Boolean isValidSignature(RestRequest request) {
        String token = authToken();
        String baseUrl = OutreachSettings.webhookBaseUrl();
        if (String.isBlank(token) || String.isBlank(baseUrl)) {
            return false; // unconfigured -> reject everything, never fail open
        }
        String provided = request.headers.get('X-Twilio-Signature');
        if (String.isBlank(provided)) {
            return false; // unsigned -> reject
        }
        String body = request.requestBody == null ? '' : request.requestBody.toString();
        Map<String, String> params = parseForm(body);
        List<String> names = new List<String>(params.keySet());
        names.sort();
        String data = baseUrl + request.requestURI;
        for (String name : names) {
            data += name + params.get(name);
        }
        Blob mac = Crypto.generateMac('hmacSHA1', Blob.valueOf(data), Blob.valueOf(token));
        return EncodingUtil.base64Encode(mac) == provided;
    }

    public static Map<String, String> parseForm(String body) {
        Map<String, String> values = new Map<String, String>();
        for (String pair : body.split('&')) {
            List<String> parts = pair.split('=', 2);
            if (parts.size() == 2) {
                values.put(
                    EncodingUtil.urlDecode(parts[0], 'UTF-8'),
                    EncodingUtil.urlDecode(parts[1], 'UTF-8')
                );
            }
        }
        return values;
    }

    /** US-region E.164 normalization, mirroring src/cleansing/normalize.py. */
    public static String normalizePhone(String raw) {
        if (String.isBlank(raw)) {
            return null;
        }
        String digits = raw.replaceAll('[^0-9]', '');
        if (digits.length() == 10) {
            return '+1' + digits;
        }
        if (digits.length() == 11 && digits.startsWith('1')) {
            return '+' + digits;
        }
        if (raw.trim().startsWith('+') && digits.length() > 11) {
            return '+' + digits;
        }
        return null;
    }

    private static String authToken() {
        if (authTokenOverride != null) {
            return authTokenOverride;
        }
        Outreach_Secret__mdt secret = Outreach_Secret__mdt.getInstance('Default');
        return secret == null ? null : secret.Twilio_Auth_Token__c;
    }
}
