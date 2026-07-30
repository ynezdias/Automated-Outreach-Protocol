/**
 * The send batch (ADR-021; replaces the daily scheduled Flow + Queueable).
 *
 * Scope is OutreachSendService.MAX_CALLOUTS_PER_RUN (90): every execute() is
 * its own transaction with a fresh 100-callout budget, so one run walks the
 * entire Ready backlog in 90-lead chunks and throughput is governed by the
 * hourly throttle and vendor caps — never by transaction limits. The per-lead
 * order (suppression -> quiet hours -> rate limit -> send -> log) is
 * untouched: each chunk delegates to OutreachSendService.sendBatch, and the
 * rate-limit check re-queries the trailing hour inside every chunk, so the
 * throttle holds across chunk boundaries.
 */
public with sharing class OutreachSendBatch implements Database.Batchable<SObject>, Database.AllowsCallouts, Database.Stateful {
    public static final Integer SCOPE_SIZE = OutreachSendService.MAX_CALLOUTS_PER_RUN;

    @TestVisible
    private Integer sentCount = 0;
    @TestVisible
    private Map<String, Integer> blockedReasons = new Map<String, Integer>();

    public Database.QueryLocator start(Database.BatchableContext context) {
        return Database.getQueryLocator(
            [SELECT Id FROM Lead WHERE Outreach_Status__c = 'Ready' ORDER BY CreatedDate]
        );
    }

    public void execute(Database.BatchableContext context, List<Lead> scope) {
        List<Id> leadIds = new List<Id>();
        for (Lead lead : scope) {
            leadIds.add(lead.Id);
        }
        for (OutreachSendService.SendOutcome outcome : OutreachSendService.sendBatch(leadIds)) {
            if (outcome.sent) {
                sentCount++;
            } else {
                Integer seen = blockedReasons.get(outcome.reason);
                blockedReasons.put(outcome.reason, seen == null ? 1 : seen + 1);
            }
        }
    }

    public void finish(Database.BatchableContext context) {
        System.debug(
            LoggingLevel.INFO,
            'OutreachSendBatch finished: sent=' + sentCount + ' blocked=' + blockedReasons
        );
    }

    public static Id run() {
        return Database.executeBatch(new OutreachSendBatch(), SCOPE_SIZE);
    }
}
