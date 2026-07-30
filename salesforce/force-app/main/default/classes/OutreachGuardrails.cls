/**
 * Deterministic pre-classifier guardrails — the Apex port of src/guardrails/
 * (the Python reference implementation, ADR-015). No ML, no network calls, no
 * state: an inbound STOP must be recognizable with AWS unreachable.
 *
 * The shared contract is the Guardrail_Truth_Table static resource
 * (byte-identical to tests/fixtures/guardrail_truth_table.csv). Both
 * implementations assert every row in CI, so semantic drift fails the build.
 *
 * Evaluation order is fixed and load-bearing:
 * opt_out -> legal_escalation -> hostility -> bounce_or_autoreply ->
 * loop_breaker -> proceed.
 *
 * Apex has no NFKC normalizer; the compatibility fold here covers the classes
 * the reference relies on (fullwidth forms, NBSP/ideographic space, zero-width
 * characters) plus the same explicit confusables table. Any gap between this
 * approximation and Python's NFKC surfaces as a truth-table disagreement.
 */
public with sharing class OutreachGuardrails {
    public static final String SUPPRESS_AND_STOP = 'suppress_and_stop';
    public static final String HUMAN_REVIEW = 'human_review';
    public static final String PROCEED = 'proceed';

    public class Result {
        public String action;
        public String rule;
        public String matchedText;

        Result(String action, String rule, String matchedText) {
            this.action = action;
            this.rule = rule;
            this.matchedText = matchedText;
        }
    }

    private static final List<String> OPT_OUT_KEYWORDS = new List<String>{
        'stop', 'stopall', 'unsubscribe', 'cancel', 'end', 'quit',
        'revoke', 'optout', 'opt out', 'remove me'
    };
    private static final Integer MIN_FUZZY_LENGTH = 4;
    private static final Pattern STOP_IMPERATIVE = Pattern.compile(
        '^(?:please )?stop (?:texting|sending|messaging|contacting|emailing|calling)\\b'
    );

    private static final List<String> LEGAL_PREFIXES = new List<String>{
        'attorney', 'lawyer', 'lawsuit', 'tcpa', 'fcc', 'complaint', 'harass'
    };
    private static final Set<String> LEGAL_TOKENS = new Set<String>{
        'sue', 'sues', 'sued', 'suing'
    };
    private static final List<String> LEGAL_PHRASES = new List<String>{ 'report you' };

    private static final Set<String> HOSTILITY_WORDS = new Set<String>{
        'fuck', 'fucking', 'shit', 'bullshit', 'asshole', 'bitch'
    };
    private static final List<String> HOSTILITY_PHRASES = new List<String>{
        'screw you', 'piss off', 'go to hell', 'leave me alone'
    };

    private static final List<String> AUTOREPLY_MARKERS = new List<String>{
        'out of office', 'out of the office', 'automatic reply', 'auto reply',
        'autoreply', 'auto submitted', 'auto generated', 'on vacation',
        'away from the office'
    };
    private static final List<String> BOUNCE_PHRASES = new List<String>{
        'delivery status notification', 'undeliverable', 'could not be delivered',
        'mail delivery failed', 'mailbox unavailable'
    };
    private static final Pattern DSN_CODE = Pattern.compile('\\b5\\.\\d\\.\\d{1,3}\\b');
    private static final Pattern SMTP_CODE = Pattern.compile('\\b55[0-4]\\b');

    // Cyrillic/Greek lookalikes, mirroring _CONFUSABLES in guardrails/text.py.
    // Keys/values are UTF-16 code points; explicit and reviewable, never inferred.
    private static final Map<Integer, Integer> CONFUSABLES = new Map<Integer, Integer>{
        1072 => 97,   // Cyrillic a  -> a
        1074 => 98,   // Cyrillic ve -> b
        1077 => 101,  // Cyrillic ie -> e
        1082 => 107,  // Cyrillic ka -> k
        1084 => 109,  // Cyrillic em -> m
        1086 => 111,  // Cyrillic o  -> o
        1088 => 112,  // Cyrillic er -> p
        1089 => 99,   // Cyrillic es -> c
        1109 => 115,  // Cyrillic dze -> s
        1090 => 116,  // Cyrillic te -> t
        1091 => 121,  // Cyrillic u  -> y
        1093 => 120,  // Cyrillic ha -> x
        1110 => 105,  // Cyrillic i  -> i
        1112 => 106,  // Cyrillic je -> j
        945 => 97,    // Greek alpha -> a
        959 => 111,   // Greek omicron -> o
        957 => 118    // Greek nu -> v
    };
    private static final Set<Integer> ZERO_WIDTH = new Set<Integer>{
        8203, 8204, 8205, 8288, 65279 // U+200B..200D, U+2060, U+FEFF
    };

    /**
     * Apply the rules table in order. Pure: no I/O, no state, no ML.
     * priorAutoReplies is the count of automated outbound replies already in
     * the thread (Lead.Auto_Reply_Count__c) — >= 2 forces human takeover.
     */
    public static Result evaluate(String body, Integer priorAutoReplies, String channel) {
        if (channel != 'sms' && channel != 'email') {
            throw new IllegalArgumentException(
                'unknown channel ' + channel + '; expected sms or email'
            );
        }
        String folded = fold(body == null ? '' : body);
        String canon = canonicalize(folded);

        String hit = matchOptOut(canon);
        if (hit != null) {
            return new Result(SUPPRESS_AND_STOP, 'opt_out', hit);
        }
        hit = matchLegal(canon);
        if (hit != null) {
            return new Result(HUMAN_REVIEW, 'legal_escalation', hit);
        }
        hit = matchHostility(canon);
        if (hit != null) {
            return new Result(HUMAN_REVIEW, 'hostility', hit);
        }
        hit = matchBounceOrAutoreply(canon, folded, channel);
        if (hit != null) {
            return new Result(HUMAN_REVIEW, 'bounce_or_autoreply', hit);
        }
        if (priorAutoReplies != null && priorAutoReplies >= 2) {
            return new Result(HUMAN_REVIEW, 'loop_breaker', '2 auto-replies in thread');
        }
        return new Result(PROCEED, null, null);
    }

    // --- text canonicalization (guardrails/text.py) -----------------------------

    @TestVisible
    private static String fold(String text) {
        List<Integer> mapped = new List<Integer>();
        for (Integer c : text.toLowerCase().getChars()) {
            if (c >= 65281 && c <= 65374) {
                mapped.add(c - 65248); // fullwidth ASCII -> ASCII (NFKC subset)
            } else if (c == 160 || c == 12288) {
                mapped.add(32); // NBSP / ideographic space -> space (NFKC subset)
            } else if (ZERO_WIDTH.contains(c)) {
                continue;
            } else if (CONFUSABLES.containsKey(c)) {
                mapped.add(CONFUSABLES.get(c));
            } else {
                mapped.add(c);
            }
        }
        return String.fromCharArray(mapped);
    }

    @TestVisible
    private static String canonicalize(String folded) {
        return folded.replaceAll('[^a-z0-9]+', ' ').trim();
    }

    /** Levenshtein distance <= 1, without building a matrix. */
    @TestVisible
    private static Boolean withinEditDistance1(String a, String b) {
        if (a == b) {
            return true;
        }
        if (Math.abs(a.length() - b.length()) > 1) {
            return false;
        }
        if (a.length() == b.length()) {
            Integer diffs = 0;
            for (Integer i = 0; i < a.length(); i++) {
                if (a.charAt(i) != b.charAt(i)) {
                    diffs++;
                }
            }
            return diffs == 1;
        }
        if (a.length() > b.length()) {
            String swap = a;
            a = b;
            b = swap;
        }
        Integer prefix = 0;
        while (prefix < a.length() && a.charAt(prefix) == b.charAt(prefix)) {
            prefix++;
        }
        return a.substring(prefix) == b.substring(prefix + 1);
    }

    // --- rules (guardrails/rules.py) --------------------------------------------

    private static Boolean fuzzyWholeMatch(String text, String keyword) {
        if (text == keyword) {
            return true;
        }
        return keyword.length() >= MIN_FUZZY_LENGTH && withinEditDistance1(text, keyword);
    }

    private static String matchOptOut(String canon) {
        String despaced = canon.replace(' ', '');
        for (String keyword : OPT_OUT_KEYWORDS) {
            if (fuzzyWholeMatch(canon, keyword)) {
                return keyword;
            }
            if (fuzzyWholeMatch(despaced, keyword.replace(' ', ''))) {
                return keyword;
            }
        }
        Matcher imperative = STOP_IMPERATIVE.matcher(canon);
        if (imperative.find()) {
            return imperative.group(0);
        }
        return null;
    }

    private static String matchLegal(String canon) {
        for (String phrase : LEGAL_PHRASES) {
            if (canon.contains(phrase)) {
                return phrase;
            }
        }
        for (String token : canon.split(' ')) {
            if (LEGAL_TOKENS.contains(token)) {
                return token;
            }
            for (String prefix : LEGAL_PREFIXES) {
                if (token.startsWith(prefix)) {
                    return prefix;
                }
            }
        }
        return null;
    }

    private static String matchHostility(String canon) {
        for (String phrase : HOSTILITY_PHRASES) {
            if (canon.contains(phrase)) {
                return phrase;
            }
        }
        for (String token : canon.split(' ')) {
            if (HOSTILITY_WORDS.contains(token)) {
                return token;
            }
        }
        return null;
    }

    private static String matchBounceOrAutoreply(String canon, String folded, String channel) {
        for (String marker : AUTOREPLY_MARKERS) {
            if (canon.contains(marker)) {
                return marker;
            }
        }
        if (channel == 'email') {
            for (String phrase : BOUNCE_PHRASES) {
                if (canon.contains(phrase)) {
                    return phrase;
                }
            }
            Matcher dsn = DSN_CODE.matcher(folded);
            if (dsn.find()) {
                return dsn.group(0);
            }
            Matcher smtp = SMTP_CODE.matcher(folded);
            if (smtp.find()) {
                return smtp.group(0);
            }
        }
        return null;
    }
}
