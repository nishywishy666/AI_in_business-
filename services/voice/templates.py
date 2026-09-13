"""Every deterministic utterance, in one file (vr_plan.md §8.2, §9.3, §6.6, Rule 6).

No model generates these. Lines quoted verbatim in the spec are used verbatim; lines the spec
describes but does not word carry a TODO(spec) so the owner can set the wording.
"""
from __future__ import annotations

# §6.1 — PRIMARY HANDLER FAILS. TODO(spec): wording not given; placeholder until the owner sets it.
FALLBACK_APOLOGY = "Sorry, we can't take your call right now. Please try again in a few minutes."

# §6.6 — spoken at CUTOVER_WARN_MS, verbatim from the spec.
CUTOVER_WARNING = "I might need to put you on hold for a second, but I've got everything so far."

# §6.6 — after a cutover, one acknowledging line built from slot state, then the current question.
# Modelled on the spec's example: "Right — I had a table for four at seven, under Sarah. What was the best email for you?"
RESUME_ACK_BOOKING = "Right — I had a table for {party_size} at {time}, under {name}."
RESUME_ACK_PARTIAL = "Right — where were we. I had {summary}."
RESUME_ACK_PLAIN = "Sorry about that — I'm back. How can I help?"
# §6.6 — resumeCount > MAX_RESUMES. TODO(spec): wording not given.
RESUME_LIMIT_GOODBYE = ("Sorry, I'm having trouble keeping the line. I'll have the owner call you back "
                        "to finish this off. Thanks for calling.")

# §8.2 booking prompts, verbatim where the spec quotes them.
ASK_DATE = "What day would you like?"
ASK_TIME = "And what time?"
ASK_PARTY_SIZE = "How many people?"
ASK_NAME = "What name is it under?"
CONFIRM_CALLER_ID = "Is the number you're calling from the best one?"
ASK_PHONE = "What's the best number for you?"
ASK_EMAIL = "And what's the best email for you?"
BOOKING_FILLER = "One moment while I lock that in."
BOOKING_DONE = "You're booked. A confirmation email is on its way to you."
BOOKING_DONE_NO_EMAIL = "You're booked. I'll have the owner follow up to confirm by email."
# §9.3 verbatim
EMAIL_READBACK = "Let me check I've got that — {spoken_email}?"
EMAIL_SPELL_REQUEST = ("I want to get this right — can you spell the part before the at symbol "
                       "for me, one letter at a time?")
EMAIL_PHONETIC_CHECK = "{letter} for {word}?"
# §8.2 — vague date / time re-asks. TODO(spec): wording not given.
REASK_DATE = "Sorry, which day exactly? For example, this Friday, or the fourteenth."
OFFER_TIMES = "We could do {first} or {second}. Which suits?"
LARGE_GROUP_CALLBACK = ("For a group that size I'll have the owner call you back to sort it out properly. "
                        "What's the best number?")
SLOT_FULL_ALTERNATIVES = "That time's full. I could do {alternatives}. Would any of those work?"
SLOT_FULL_NO_ALTERNATIVES = "That time's full and I can't see another slot nearby. I'll have the owner call you back."
CONFIRM_BOOKING = "So that's a table for {party_size} on {date} at {time}, under {name}. Shall I lock that in?"

# Rule 1 / Rule 6 — lookup miss and model-failure fallbacks. TODO(spec): wording not given.
WILL_CHECK_AND_CALLBACK = "I'm not sure about that one — I'll check with the owner and have them call you back. What's the best number?"
CALLBACK_TAKEN = "Thanks, I've passed that on. Someone will call you back shortly."
ALLERGEN_UNKNOWN = "I can't confirm that allergen for this dish, so I won't guess. I'll have the owner call you back to check."
ROUTER_FAILURE = "Sorry, I didn't quite catch that. I'll have someone call you back to help. What's the best number?"
CLOSING = "Thanks for calling. Bye for now."

# §7.2 Gemini timeout — flat template fallbacks per fact/menu shape, verbatim example from the spec.
FACT_HOURS = "We're open {hours} on {day}."
FACT_GENERIC = "{value}."
MENU_ITEM_PRICE = "The {name} is {price}."
MENU_ITEM_NO_PRICE = "I don't have a confirmed price for the {name}, so I'll have the owner call you back."
MENU_SECTION = "In {section} we've got {items}. What are you after?"
ALLERGEN_STATUS = "For the {name}: {statuses}."
# §4 CHITCHAT fallback when Gemini is unavailable. TODO(spec): wording not given.
CHITCHAT_FALLBACK = "Happy to help. What would you like to know?"
# §7.1 low-confidence, nothing in progress, lookup missed. TODO(spec): wording not given.
DID_NOT_UNDERSTAND = "Sorry, I didn't catch that. You can ask about the menu, our hours, or book a table."
