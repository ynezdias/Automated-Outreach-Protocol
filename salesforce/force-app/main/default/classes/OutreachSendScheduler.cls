/**
 * Hourly trigger for OutreachSendBatch. Scheduled Apex replaces the daily
 * Flow because Flows cannot schedule sub-daily (ADR-021). Register once per
 * org (see runbook):
 *
 *   OutreachSendScheduler.scheduleHourly();
 *
 * Running every hour around the clock is safe: quiet hours are enforced
 * per-lead inside the batch, so off-window leads are deferred with reason
 * quiet_hours and picked up by a later run.
 */
public with sharing class OutreachSendScheduler implements Schedulable {
    public static final String HOURLY_CRON = '0 0 * * * ?';
    public static final String JOB_NAME = 'Outreach Hourly Send';

    public void execute(SchedulableContext context) {
        OutreachSendBatch.run();
    }

    public static Id scheduleHourly() {
        return System.schedule(JOB_NAME, HOURLY_CRON, new OutreachSendScheduler());
    }
}
