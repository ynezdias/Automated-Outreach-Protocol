/**
 * Mirrors a guardrail opt-out to the AWS suppression list (ADR-020).
 *
 * Salesforce is written synchronously inside the webhook transaction — that
 * write gates the next send, so suppression enforcement never depends on this
 * job. This job only propagates the event to the lake-side list (60-second
 * bound, ADR-009) via the AWS_Suppression Named Credential (SigV4).
 *
 * Failure behaviour: retries with growing delay up to MAX_ATTEMPTS; on
 * terminal failure the source message is flagged Needs_Review__c with the
 * error recorded, so a human closes the loop. The nightly vendor
 * reconciliation is a further backstop.
 */
public with sharing class OutreachSuppressionSyncQueueable implements Queueable, Database.AllowsCallouts {
    @TestVisible
    private static final Integer MAX_ATTEMPTS = 5;

    private final String identifier;
    private final String reason;
    private final Id messageId;
    private final Integer attempt;

    public OutreachSuppressionSyncQueueable(
        String identifier, String reason, Id messageId, Integer attempt
    ) {
        this.identifier = identifier;
        this.reason = reason;
        this.messageId = messageId;
        this.attempt = attempt;
    }

    public void execute(QueueableContext context) {
        HttpRequest request = new HttpRequest();
        request.setEndpoint('callout:AWS_Suppression/suppressions');
        request.setMethod('POST');
        request.setHeader('Content-Type', 'application/json');
        request.setBody(
            JSON.serialize(
                new Map<String, Object>{
                    'identifier' => identifier,
                    'reason' => reason,
                    'source' => 'salesforce_inbound_guardrail',
                    'occurred_at' => Datetime.now().formatGmt('yyyy-MM-dd\'T\'HH:mm:ss') +
                        '+00:00'
                }
            )
        );
        Integer status;
        try {
            status = new Http().send(request).getStatusCode();
        } catch (Exception error) {
            status = -1;
        }
        if (status >= 200 && status < 300) {
            return;
        }
        if (attempt + 1 < MAX_ATTEMPTS) {
            // Chaining is illegal inside Apex tests; the retry path is covered by
            // asserting terminal behaviour with attempt = MAX_ATTEMPTS - 1.
            if (!Test.isRunningTest()) {
                System.enqueueJob(
                    new OutreachSuppressionSyncQueueable(
                        identifier, reason, messageId, attempt + 1
                    ),
                    Math.min(attempt + 1, 10) // minutes of backoff
                );
            }
            return;
        }
        update new Outreach_Message__c(
            Id = messageId,
            Needs_Review__c = true,
            Send_Error__c = 'AWS suppression sync failed after ' + MAX_ATTEMPTS + ' attempts'
        );
    }
}
