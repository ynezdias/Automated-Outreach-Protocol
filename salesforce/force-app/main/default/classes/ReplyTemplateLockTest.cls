/**
 * Compliance tests for the Reply_Template__c immutability invariants:
 * Approved never returns to Draft, and an Approved record's Body__c never
 * mutates in place.
 */
@IsTest
private class ReplyTemplateLockTest {
    private static Reply_Template__c makeApprovedTemplate() {
        Reply_Template__c template = new Reply_Template__c(
            Name = 'Interested v1',
            Status__c = 'Draft',
            Body__c = 'Thanks for your interest — here is how our financing works.',
            Version__c = 1,
            Intent__c = 'Interested'
        );
        insert template;
        template.Status__c = 'Approved';
        update template;
        return template;
    }

    @IsTest
    static void approvedCannotReturnToDraft() {
        Reply_Template__c template = makeApprovedTemplate();
        template.Status__c = 'Draft';
        try {
            update template;
            Assert.fail('Approved -> Draft must be blocked by validation rule');
        } catch (DmlException e) {
            Assert.isTrue(
                e.getMessage().contains('cannot move back to Draft'),
                'Expected the Approved_Cannot_Return_To_Draft error, got: ' + e.getMessage()
            );
        }
    }

    @IsTest
    static void approvedBodyIsImmutable() {
        Reply_Template__c template = makeApprovedTemplate();
        template.Body__c = 'Edited body';
        try {
            update template;
            Assert.fail('Editing an Approved body must be blocked by validation rule');
        } catch (DmlException e) {
            Assert.isTrue(
                e.getMessage().contains('cannot be changed'),
                'Expected the Approved_Body_Is_Immutable error, got: ' + e.getMessage()
            );
        }
    }

    @IsTest
    static void approvedBodyStaysLockedEvenWhileRetiring() {
        // Sneaking a body edit into the same update that retires the record
        // must still be blocked: PRIORVALUE(Status__c) is Approved.
        Reply_Template__c template = makeApprovedTemplate();
        template.Status__c = 'Retired';
        template.Body__c = 'Edited on the way out';
        try {
            update template;
            Assert.fail('Body edit during retirement must be blocked');
        } catch (DmlException e) {
            Assert.isTrue(
                e.getMessage().contains('cannot be changed'),
                'Expected the Approved_Body_Is_Immutable error, got: ' + e.getMessage()
            );
        }
    }

    @IsTest
    static void draftBodyIsEditableAndApprovedCanRetire() {
        Reply_Template__c template = new Reply_Template__c(
            Name = 'Question v1',
            Status__c = 'Draft',
            Body__c = 'Draft body',
            Version__c = 1,
            Intent__c = 'Question'
        );
        insert template;
        template.Body__c = 'Revised draft body';
        update template; // drafts are editable
        template.Status__c = 'Approved';
        update template;
        template.Status__c = 'Retired';
        update template; // Approved -> Retired is the sanctioned exit
        Reply_Template__c reloaded = [
            SELECT Status__c, Body__c FROM Reply_Template__c WHERE Id = :template.Id
        ];
        Assert.areEqual('Retired', reloaded.Status__c);
        Assert.areEqual('Revised draft body', reloaded.Body__c);
    }
}
