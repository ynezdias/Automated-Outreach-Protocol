# TAXONOMY.md — Reply-intent taxonomy, reconciled against real data

Sources reconciled (per the work order):
(a) the provisional 12-value `Outreach_Intent` Global Value Set (ADR-011);
(b) the original plan document's taxonomy — **not available in this repo**; ADR-011
    was derived as its stand-in and is treated as (b) here (open question OQ-1);
(c) `texttorrent_conversations_last30d.csv`.

**Finding that reframes deliverable 1: the dataset contains no labels.** The file
is a raw conversation export (156,251 message rows across 27,615 conversations
and 5 rep accounts; the "237,290" figure is physical file lines, inflated by
multiline message bodies). Its columns are routing metadata plus `direction`,
`sender_role`, and `message` — there is no intent column of any kind, and no
other labeled file exists on this machine. So "every distinct label present in
the 237k" is: **none**. What the dataset does support is an empirical frequency
analysis of the 39,353 inbound replies (28,047 distinct after whitespace/case
normalization), which is what everything below is grounded in. The class-size
figures are **heuristic-bucket estimates** (deterministic keyword/pattern rules
plus the production guardrails run over every distinct reply) — good enough to
size classes and kill weak ones, not a substitute for the labeling pass this
document is the guide for.

## 1. What is actually in the data (estimates, deduplicated)

| Empirical bucket | Distinct bodies | Total messages | Note |
| --- | ---: | ---: | --- |
| Long-tail substantive replies ("other") | 17,463 | 22,165 | mostly mid-funnel deal talk |
| Ends with "?" (questions) | 3,238 | 3,872 | incl. "Any update?", "Who is this?" |
| Contains an email address | 3,416 | 3,798 | prospect handing over contact info |
| Bare amount ("50k", "$100,000") | 569 | 2,833 | answers to the first-touch question |
| Asks for email / info | 1,361 | 1,592 | "Email me", "send me info" |
| Short affirmative ("Yes", "Ok") | 8 | 1,527 | "Yes" alone appears 694× |
| Process update ("Sent", "Done") | 859 | 1,336 | docs/application submitted |
| Guardrail: hostility | 553 | 895 | "Fuck off" 186× |
| "Not interested" family | 506 | 909 | plus bare "No" 385× in the tail |
| Wrong number/person | 255 | 600 | "Wrong number" 247× |
| Call requests | 358 | 538 | "Call me" 79×; text-incapable lines |
| Identity questions | 174 | 433 | "Who is this?" |
| Guardrail: opt-out keywords | 151 | 313 | "Stop" 52× — see OQ-5 |
| Guardrail: legal escalation | 140 | 172 | incl. formal TCPA revocation notices |
| Not-now family | 75 | 101 | "Not yet" 24× |
| Guardrail: bounce/auto-reply | 60 | 63 | OOO, landline auto-responses |
| Already-funded family | 78 | 80 | |
| Referral to someone else | 34 | 38 | |

## 2. Mapping: ADR-011 provisional set → proposed final set

| ADR-011 value | Disposition | Why |
| --- | --- | --- |
| Interested | **Keep** | Massive support (short affirmatives + engaged replies). |
| Question | **Keep** (absorbs identity questions) | 3.9k+ observed; "Who is this?" is a Question subtype with its own template, not its own class. |
| Request_More_Info | **Keep** | 1.6k observed asks for email/info. |
| Not_Interested | **Keep** (absorbs Not_Now, Already_Financed) | 1.3k+ observed incl. bare "No". |
| Not_Now | **Merge → Not_Interested** | 75 unique < 150. Recorded as a boundary case, not a class. |
| Already_Financed | **Merge → Not_Interested** | 78 unique < 150. Same. |
| Wrong_Person | **Keep** | 255 unique, sharply patterned. |
| Referral | **Remove** | 34 unique < 150 and heterogeneous. OQ-2. |
| Opt_Out | **Keep** (guardrail-detected, never ML-predicted) | Per work order and ADR-011: one consistent label on records. |
| Legal_Escalation | **Keep** (guardrail-detected) | 140 unique — below 150, kept by the same guardrail-consistency rule. |
| Auto_Reply | **Keep** (guardrail-detected) | 60 unique — kept by the same rule; the guardrail, not the model, produces it. |
| Unclear | **Keep** | The honest bucket; routes to human. |
| — | **Add Amount_Given** | 569 unique / 2,833 messages. Distinct surface form, distinct next action. |
| — | **Add Call_Request** | 358 unique / 538 messages, incl. text-incapable lines. |
| — | **Add Process_Update** | 859 unique / 1,336 messages of mid-funnel "Sent/Done/Submitted". |
| — | **Add Hostile** | 553 unique / 895 messages. ADR-011 had no consistent label for what the hostility guardrail catches. |

Final proposed set (13): Interested, Amount_Given, Question, Request_More_Info,
Call_Request, Process_Update, Not_Interested, Wrong_Person, Hostile, Opt_Out,
Legal_Escalation, Auto_Reply, Unclear.

## 3. Open questions (not guessed)

- **OQ-1** — The original plan document's taxonomy was never in the repo. If it
  differs from ADR-011's 12, supply it and this mapping gets a third column.
- **OQ-2 (Referral)** — 34 unique examples, ranging from "let me ask my partner"
  (a positive signal) to full hand-off requests. Labelers put these in Unclear
  for v1; revisit if labeling surfaces > 150 clean cases.
- **OQ-3 (channel-switch requests)** — "Please EMAIL me. No Calls or Texts." is
  Request_More_Info by content but is also an explicit revocation of SMS
  contact preference. Does policy treat it as a suppression event for SMS while
  keeping email consent? Compliance call, not a labeling call.
- **OQ-4 (wrong-number suppression)** — "Wrong number" strongly implies we lack
  consent for that number. Should Wrong_Person auto-add the number to the
  suppression list in addition to the apology template? Recommended yes;
  needs an explicit decision.
- **OQ-5 (low STOP volume)** — only 313 STOP-family messages in 39k replies
  suggests the carrier/TextTorrent intercepts most STOPs before the inbox
  export. Confirms Conflict B's reconciliation matters: their blocked list sees
  opt-outs this dataset doesn't.
- **OQ-6 (thumbs-up reactions)** — replies like `👍 to "…"` are reactions, not
  text. Labelers treat the reaction as the message ("👍" = Interested when
  reacting to an offer); tooling must not strip the emoji.

## 4. Below the 150-unique bar (not separate classes)

Not_Now (75), Already_Financed (78), Referral (34). Legal_Escalation (140) and
Auto_Reply (60) are also below the bar but are guardrail-produced labels, not
ML classes — the classifier never has to learn them, so the sample-size
argument doesn't apply (they are excluded from training or downweighted per
the training design, never auto-replied either way).

## 5. Auto-reply eligibility

| Intent | auto_reply_eligible | Reasoning |
| --- | --- | --- |
| Interested | **true** | Core value path; acknowledge + qualifying question template. |
| Amount_Given | **true** | Highest-value automation: amount acknowledged, next qualifying step. Precision here is what the ≥0.97 threshold protects. |
| Question | **false** (v1) | Answering questions with templates risks answering the wrong question. Human until template coverage of the top question types is proven. |
| Request_More_Info | **true** | Sending the approved info template is exactly what was asked for. |
| Call_Request | **false** | The correct response is a phone call — an action, not a message. Auto-promising a call nobody makes is worse than silence. |
| Process_Update | **false** | Mid-funnel; requires knowing what "Sent" refers to. Rep context. |
| Not_Interested | **true** | One approved polite close, then done. Never a second attempt (loop rules still apply). |
| Wrong_Person | **true** | Approved apology + removal template; see OQ-4 for the suppression side. |
| Hostile | **false** | Guardrail-routed to human; any automated reply is escalation fuel. |
| Opt_Out | **false** | Guardrail/regex handles suppression; no reply is sent (carrier confirms STOP). Never ML-predicted. |
| Legal_Escalation | **false** | Human, flagged, always (hard invariant). |
| Auto_Reply | **false** | Robots don't need replies; replying restarts loops. |
| Unclear | **false** | By definition. |

## 6. Definitions and boundary cases (the labeling guide)

Label the **inbound message in its thread context** (the export provides full
threads). When two labels seem to apply, the earlier rule below wins.
Guardrail labels (Opt_Out, Legal_Escalation, Hostile, Auto_Reply) are applied
by the deterministic guardrails; labelers use them only when hand-labeling
historical data, with the same precedence order as the guardrails.

**Opt_Out** — whole-message STOP-family keyword or an imperative "stop
texting/contacting" opening; also explicit "remove me from your list".
Examples: "Stop" (52×) · "Stop texting me" (34×) · "Stop 🛑" (31×).
Boundary: "Stop by our office next week" is NOT opt-out (Interested/Question);
"Stop Already funded" IS opt-out (leading STOP wins); "Please EMAIL me, no
calls or texts" is NOT Opt_Out (see OQ-3) — it's Request_More_Info.

**Legal_Escalation** — attorney/lawsuit/TCPA/FCC/harassment/DNC language,
formal consent revocation, threats to report.
Examples: "Stop f***ing harassing me" (21×) · "This number is on the national
DNC registry. Immediately cease contact…" · "LEGAL NOTICE: I revoke any
consent to be contacted…".
Boundary: profanity + legal wording = Legal_Escalation, never Hostile (legal
wins). A bare "you're harassing me" counts; "my lawyer says I should get a
loan" does not (Question/Interested).

**Hostile** — profanity or aggression directed at us without legal wording.
Examples: "Fuck off" (186×) · "Fuck you" (54×) · "Leave me alone" (18×).
Boundary: "this is bullshit rates are too high" is Hostile even though it
carries deal content; profanity about their own situation ("my credit is
shit") is NOT hostile — label by target of the aggression.

**Auto_Reply** — machine-generated: out-of-office, vacation responders,
landline "can't receive texts" system messages, disconnected-number notices.
Examples: "Thanks for your message. I am out of the office until August 6…" ·
"AUTO REPLY: I have a new phone number…" · "I'm on vacation until Monday".
Boundary: a human typing "on vacation, text me next week" reads identical —
when in doubt about human vs machine, prefer Auto_Reply only if it carries
responder boilerplate (subject-style prefix, alternate-contact instructions).

**Amount_Given** — the reply is (essentially only) a funding amount, answering
"how much capital are you looking for?".
Examples: "50k" (157×) · "100k" (140×) · "150k" (97×).
Boundary: "50k but only monthly payments" is Amount_Given (amount + a
constraint); "we need 20 million to buy amo contracts…" with a life story is
Interested (substantive engagement, not a bare amount); a phone number or
ZIP that pattern-matches digits is Unclear — labelers read, not regex.

**Interested** — engagement that moves toward the offer: short affirmatives
answering our question, requests to proceed, positive substance.
Examples: "Yes" (694×) · "Ok" (486×) · "Sure" (120×).
Boundary: "Yes" replying to "do you want us to stop?" is Opt_Out-adjacent —
thread context governs; "Ok" acknowledging a rep's "I'll call you tomorrow"
is Process_Update territory, not new interest. When an affirmative answers
our first-touch question, it is Interested.

**Question** — asks us something: rates, terms, identity, legitimacy, process.
Examples: "Who is this?" (79×) · "Any update?" (22×) · "What are your rates?".
Boundary: "Who is this?" and "How did you get my number?" are Question (with
a mandatory-disclosure template later), not Hostile — unless combined with
aggression. "Any update?" from a mid-funnel thread may be Process_Update if
it references documents already sent; identity/product questions stay here.

**Request_More_Info** — asks us to send something: info, website, email,
company profile.
Examples: "Email" (37×) · "What's your email" (15×) · "Ok send me your email a
website and company profile…" (17×).
Boundary: providing THEIR email address unprompted is Request_More_Info
(they're inviting contact); "email me, no calls or texts" also carries a
channel preference (OQ-3).

**Call_Request** — asks for a phone call or reports a text-incapable line.
Examples: "Call me" (79×) · "Sorry! I can't get text messages on this phone.
Please call me." (22×) · "Give me a call" (7×).
Boundary: "don't call me" is NOT Call_Request (Not_Interested or Opt_Out
family by wording); a number-only reply is Amount_Given/Unclear, not an
implied call request.

**Process_Update** — status about an in-flight application/documents.
Examples: "Sent" (202×) · "Done" (169×) · "Submitted" (22×).
Boundary: only meaningful mid-thread; the same words as a first reply to a
first touch are Unclear. "Just sent the statements, when do I hear back?" is
Process_Update (the question is about the process already in motion).

**Not_Interested** — declines: "no", "not interested", "we're good", and the
merged families: bad timing ("not yet", "check back in a year") and already
financed ("already funded", "we have funding").
Examples: "No" (385×) · "Not interested" (136×) · "No thanks" (123×).
Boundary: "not right now, maybe Q4" is Not_Interested (timing note goes in
CRM, not the label); "no daily-payment products" is Interested-with-constraints
(they're negotiating terms, not declining); repeated "no" after a decline that
was already answered escalates to Hostile territory only if aggressive.

**Wrong_Person** — the recipient is not the prospect: wrong number, person no
longer there.
Examples: "Wrong number" (247×) · "Wrong person" (21×) · "You have the wrong
number" (20×).
Boundary: "Tim doesn't work here anymore" is Wrong_Person; "talk to my
partner instead" is NOT (OQ-2/Unclear); wrong-number + rage ("WRONG NUMBER!!!
STOP HARASSING ME") is Legal_Escalation by precedence (harass).

**Unclear** — everything that fits nothing above: empty bodies, "?", lone
emoji without context, referral hand-offs (OQ-2), cross-talk.
Examples: "" (268×) · "?" (102×) · "Hello?" (15×).
Boundary: Unclear is a real label, not a failure — the model must learn to
route these to a human. Do not force-fit.

## 7. What changed in metadata

`Outreach_Intent` Global Value Set updated to the 13 values above (ADR-022):
`Not_Now` and `Already_Financed` removed (merged), `Referral` removed,
`Amount_Given`, `Call_Request`, `Process_Update`, `Hostile` added. Done before
any labeling or training so picklist API names are stable from here on.
