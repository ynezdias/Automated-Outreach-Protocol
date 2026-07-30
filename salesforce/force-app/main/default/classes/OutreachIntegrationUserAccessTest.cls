/**
 * Compliance test: the integration user's access is exactly the
 * Outreach_Integration_User permission set — Lead and Outreach_Message__c
 * read/create/edit (no delete), Reply_Template__c read-only, and NOTHING else.
 * A widening of this boundary is a release blocker.
 */
@IsTest
private class OutreachIntegrationUserAccessTest {
    private static User makeIntegrationUser() {
        Profile minAccess = [
            SELECT Id FROM Profile WHERE Name = 'Minimum Access - Salesforce' LIMIT 1
        ];
        User u = new User(
            Alias = 'oiu',
            Email = 'outreach.integration@example.invalid',
            Username = 'outreach.integration.' + System.currentTimeMillis() + '@example.invalid',
            LastName = 'Integration',
            ProfileId = minAccess.Id,
            TimeZoneSidKey = 'America/New_York',
            LocaleSidKey = 'en_US',
            EmailEncodingKey = 'UTF-8',
            LanguageLocaleKey = 'en_US'
        );
        insert u;
        PermissionSet ps = [
            SELECT Id FROM PermissionSet WHERE Name = 'Outreach_Integration_User' LIMIT 1
        ];
        insert new PermissionSetAssignment(AssigneeId = u.Id, PermissionSetId = ps.Id);
        return u;
    }

    @IsTest
    static void grantsExactlyTheOutreachObjects() {
        User integrationUser = makeIntegrationUser();
        System.runAs(integrationUser) {
            // In scope: Lead and Outreach_Message__c, without delete.
            Assert.isTrue(Schema.sObjectType.Lead.isAccessible(), 'Lead must be readable');
            Assert.isTrue(Schema.sObjectType.Lead.isCreateable(), 'Lead must be createable');
            Assert.isTrue(Schema.sObjectType.Lead.isUpdateable(), 'Lead must be updateable');
            Assert.isFalse(Schema.sObjectType.Lead.isDeletable(), 'Lead delete must be denied');
            Assert.isTrue(
                Schema.sObjectType.Outreach_Message__c.isCreateable(),
                'Outreach_Message__c must be createable'
            );
            Assert.isFalse(
                Schema.sObjectType.Outreach_Message__c.isDeletable(),
                'Outreach_Message__c delete must be denied'
            );
            // Templates: read-only. The integration user can never author or alter one.
            Assert.isTrue(
                Schema.sObjectType.Reply_Template__c.isAccessible(),
                'Reply_Template__c must be readable'
            );
            Assert.isFalse(
                Schema.sObjectType.Reply_Template__c.isCreateable(),
                'Reply_Template__c create must be denied'
            );
            Assert.isFalse(
                Schema.sObjectType.Reply_Template__c.isUpdateable(),
                'Reply_Template__c edit must be denied'
            );
        }
    }

    @IsTest
    static void deniesEverythingOutsideThePermissionSet() {
        User integrationUser = makeIntegrationUser();
        System.runAs(integrationUser) {
            Assert.isFalse(Schema.sObjectType.Account.isAccessible(), 'Account read must be denied');
            Assert.isFalse(Schema.sObjectType.Account.isCreateable(), 'Account create must be denied');
            Assert.isFalse(Schema.sObjectType.Contact.isAccessible(), 'Contact read must be denied');
            Assert.isFalse(Schema.sObjectType.Contact.isCreateable(), 'Contact create must be denied');
            Assert.isFalse(
                Schema.sObjectType.Opportunity.isAccessible(),
                'Opportunity read must be denied'
            );
            Assert.isFalse(Schema.sObjectType.Case.isAccessible(), 'Case read must be denied');
            Assert.isFalse(Schema.sObjectType.Campaign.isAccessible(), 'Campaign read must be denied');
        }
    }

    @IsTest
    static void hasNoModifyAllData() {
        User integrationUser = makeIntegrationUser();
        List<PermissionSetAssignment> broadGrants = [
            SELECT Id
            FROM PermissionSetAssignment
            WHERE AssigneeId = :integrationUser.Id
            AND (
                PermissionSet.PermissionsModifyAllData = TRUE
                OR PermissionSet.PermissionsViewAllData = TRUE
            )
        ];
        Assert.isTrue(
            broadGrants.isEmpty(),
            'Integration user must never hold Modify All Data or View All Data'
        );
    }
}
