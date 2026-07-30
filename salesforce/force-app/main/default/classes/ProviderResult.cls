/**
 * Result of a messaging-provider send. errorCode is null on success; on
 * failure it carries enough detail (status code + body) to log and diagnose.
 */
public with sharing class ProviderResult {
    public String messageId;
    public String status;
    public String errorCode;

    public ProviderResult(String messageId, String status, String errorCode) {
        this.messageId = messageId;
        this.status = status;
        this.errorCode = errorCode;
    }
}
