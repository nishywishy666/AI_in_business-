"""Verbatim disclosure text (vr_plan.md §7.4, Rules 2 and 3).

GREETING_DISCLOSURE is pre-rendered to audio by scripts/render_greeting.py and played first on
every call; it is never model-generated and never skippable. CROSS_CONTACT is appended by Python
after every allergen answer; the model never writes it and never omits it.
"""

GREETING_DISCLOSURE = (
    "Thanks for calling {business_name}. You're speaking with an AI assistant, "
    "this call is recorded, and it's processed using third-party AI services. "
    "How can I help?"
)

CROSS_CONTACT = (
    "I should say that we prepare everything in the same kitchen, "
    "so we can't guarantee any dish is completely free of traces."
)
