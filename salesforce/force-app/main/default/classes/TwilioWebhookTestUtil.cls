/**
 * Shared helpers for webhook tests: configures a known signing token and
 * builds requests signed exactly the way Twilio signs them.
 */
@IsTest
public class TwilioWebhookTestUtil {
    public static final String TEST_TOKEN = 'test_auth_token_1234567890';
    public static final String BASE_URL = 'https://outreach.example.my.salesforce-sites.com/services/apexrest';

    public static void configure() {
        TwilioWebhook.authTokenOverride = TEST_TOKEN;
        OutreachSettings.overrideRecord = new Outreach_Setting__mdt(
            Webhook_Base_Url__c = BASE_URL,
            Per_Number_Hourly_Limit__c = 50
        );
    }

    public static RestRequest signedRequest(String uri, String formBody) {
        RestRequest request = unsignedRequest(uri, formBody);
        request.addHeader('X-Twilio-Signature', sign(uri, formBody, TEST_TOKEN));
        return request;
    }

    public static RestRequest unsignedRequest(String uri, String formBody) {
        RestRequest request = new RestRequest();
        request.httpMethod = 'POST';
        request.requestURI = uri;
        request.requestBody = Blob.valueOf(formBody);
        return request;
    }

    public static String sign(String uri, String formBody, String token) {
        Map<String, String> params = TwilioWebhook.parseForm(formBody);
        List<String> names = new List<String>(params.keySet());
        names.sort();
        String data = BASE_URL + uri;
        for (String name : names) {
            data += name + params.get(name);
        }
        return EncodingUtil.base64Encode(
            Crypto.generateMac('hmacSHA1', Blob.valueOf(data), Blob.valueOf(token))
        );
    }
}
