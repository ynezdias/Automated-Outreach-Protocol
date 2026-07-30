/**
 * Invocable entry point for the scheduled send Flow. Enqueues a Queueable
 * because callouts are not permitted inside the Flow's own transaction.
 */
public with sharing class OutreachSendInvocable {
    @InvocableMethod(
        label='Send Outreach Messages'
        description='Queues the outreach send path (suppression -> quiet hours -> rate limit -> send -> log) for the given Leads.'
    )
    public static void send(List<Id> leadIds) {
        if (!leadIds.isEmpty()) {
            System.enqueueJob(new OutreachSendQueueable(leadIds));
        }
    }
}
