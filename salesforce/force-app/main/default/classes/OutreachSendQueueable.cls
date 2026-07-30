/**
 * Async executor for the send path; allows callouts.
 */
public with sharing class OutreachSendQueueable implements Queueable, Database.AllowsCallouts {
    private final List<Id> leadIds;

    public OutreachSendQueueable(List<Id> leadIds) {
        this.leadIds = leadIds;
    }

    public void execute(QueueableContext context) {
        OutreachSendService.sendBatch(leadIds);
    }
}
