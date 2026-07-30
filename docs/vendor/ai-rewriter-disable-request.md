# Vendor request: disable AI message rewriting (account-wide)

Status: **DRAFT — not yet sent.** Send from ydias@fundmatellc.com and file the
written reply in this directory. ADR-018 is the governing decision; the daily
canary keeps running regardless of the vendor's answer.

---

To: Dev@texttorrent.com
Subject: Disable AI message "cleaning"/rewriting account-wide — written confirmation requested

Hi TextTorrent team,

Your API documentation (section 5.9, Send Message) states that messages are
"automatically cleaned using AI to fix encoding issues." We operate regulated
B2B financing outreach: every outbound message must be transmitted
byte-for-byte as submitted, because our compliance controls (pre-approved
message templates and audit reconstruction of every send) depend on the
delivered text matching the approved text exactly.

For our account (SID ending [fill in last 4]), please:

1. Disable all AI-based message rewriting / cleaning / "clarity and
   compliance" processing account-wide — for API sends and any other send
   path.
2. Confirm in writing that it is disabled.
3. Tell us whether any future product change could re-enable it without our
   explicit opt-in.

To be clear, this concerns the send path only. We do not use the optional
"Generate AI Replies" feature and are not asking about it.

Thank you,
[name]
FundMate LLC
