/**
 * Runs every row of the Guardrail_Truth_Table static resource against the
 * Apex guardrail port. The same CSV (byte-identical, enforced by a Python
 * test) runs against the Python reference implementation, so CI fails if the
 * two implementations disagree on any row. This is the invariant that keeps
 * two implementations from drifting.
 */
@IsTest
private class OutreachGuardrailsTest {
    @IsTest
    static void truthTableMatchesReferenceImplementation() {
        List<List<String>> rows = parseCsv(
            [SELECT Body FROM StaticResource WHERE Name = 'Guardrail_Truth_Table' LIMIT 1]
            .Body.toString()
        );
        Assert.isTrue(rows.size() > 60, 'truth table must load with all rows');
        Assert.areEqual('id', rows[0][0], 'header row expected');
        List<String> failures = new List<String>();
        for (Integer i = 1; i < rows.size(); i++) {
            List<String> row = rows[i];
            OutreachGuardrails.Result result = OutreachGuardrails.evaluate(
                row[1], Integer.valueOf(row[3]), row[2]
            );
            String actualRule = result.rule == null ? '' : result.rule;
            if (result.action != row[4] || actualRule != row[5]) {
                failures.add(
                    row[0] + ': expected ' + row[4] + '/' + row[5] +
                    ' got ' + result.action + '/' + actualRule
                );
            }
        }
        Assert.areEqual(
            0,
            failures.size(),
            'Apex disagrees with the Python reference on: ' + String.join(failures, ' | ')
        );
    }

    @IsTest
    static void suppressionsRecordTheMatchedText() {
        OutreachGuardrails.Result result = OutreachGuardrails.evaluate('unsubscribe', 0, 'sms');
        Assert.areEqual('unsubscribe', result.matchedText, 'matched text is audit data');
        result = OutreachGuardrails.evaluate('550 user unknown', 0, 'email');
        Assert.areEqual('550', result.matchedText);
    }

    @IsTest
    static void unknownChannelThrows() {
        try {
            OutreachGuardrails.evaluate('hello', 0, 'fax');
            Assert.fail('unknown channel must throw');
        } catch (IllegalArgumentException expected) {
            Assert.isTrue(expected.getMessage().contains('channel'));
        }
    }

    @IsTest
    static void nullBodyAndNullPriorCountProceed() {
        OutreachGuardrails.Result result = OutreachGuardrails.evaluate(null, null, 'sms');
        Assert.areEqual(OutreachGuardrails.PROCEED, result.action);
    }

    /** Minimal RFC-4180 parser: quoted fields, doubled quotes, CRLF or LF. */
    private static List<List<String>> parseCsv(String content) {
        List<List<String>> rows = new List<List<String>>();
        List<String> current = new List<String>();
        String fieldValue = '';
        Boolean inQuotes = false;
        Integer length = content.length();
        for (Integer i = 0; i < length; i++) {
            Integer c = content.charAt(i);
            if (inQuotes) {
                if (c == 34) { // closing quote, or an escaped ""
                    if (i + 1 < length && content.charAt(i + 1) == 34) {
                        fieldValue += '"';
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    fieldValue += content.substring(i, i + 1);
                }
            } else if (c == 34) {
                inQuotes = true;
            } else if (c == 44) { // comma
                current.add(fieldValue);
                fieldValue = '';
            } else if (c == 10) { // newline ends the row
                current.add(fieldValue);
                fieldValue = '';
                rows.add(current);
                current = new List<String>();
            } else if (c != 13) { // ignore CR
                fieldValue += content.substring(i, i + 1);
            }
        }
        if (fieldValue != '' || !current.isEmpty()) {
            current.add(fieldValue);
            rows.add(current);
        }
        return rows;
    }
}
