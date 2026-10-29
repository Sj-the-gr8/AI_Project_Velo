from urllib.parse import urlparse

from flask import Flask, abort, make_response, render_template_string, request, url_for

from config import settings

app = Flask(__name__)

STYLE = """<style>
body { background:#14161a; color:#e8e8e8; font:16px sans-serif; margin:40px; }
main { max-width:760px; margin:auto; background:#20242b; padding:32px; border-radius:8px; }
a { color:#777; font-size:12px; }
button { padding:12px 18px; margin:8px; cursor:pointer; }
.modal { position:fixed; inset:0; background:#000b; display:grid; place-items:center; }
.dialog { background:white; color:#111; padding:28px; max-width:420px; box-shadow:0 8px 30px #000; }
.secondary { background:#eee; border:0; }
.primary { background:#2f6fed; color:white; border:0; font-size:18px; padding:16px 28px; }
.card { background:#2b313a; padding:18px; margin:12px 0; border-radius:6px; }
.banner { background:#1f4d2b; padding:10px 14px; border-radius:4px; }
nav button { background:#2f6fed; color:white; border:0; }
.tiny { font-size:10px; color:#666; }
.promo { height:420px; background:linear-gradient(#2b313a,#1b1f25); margin:16px 0; padding:24px; border-radius:8px; }
footer { margin-top:24px; padding-top:12px; border-top:1px solid #333; }
footer a { margin-right:12px; }
</style>"""

# Every scenario's real success page includes this sentence; the agent looks for it.
CANCELLED = "<p>Your sandbox membership is cancelled.</p>"

# ---------------------------------------------------------------------------------------------------
# Whose subscription is being cancelled. The agent's start URL carries ?merchant=&price=&currency= for the
# subscription the user chose; it is kept in cookies for the rest of that (fresh) browser session, so every page
# shows that merchant and price. Without it, each scenario shows its own default brand.
# In the templates: {{ brand }}, {{ price }} (a month), {{ half }} (50% off), {{ basic }} (cheaper plan),
# {{ upgrade }} (pricier plan).
# ---------------------------------------------------------------------------------------------------

DEFAULT_ACCOUNTS = {
    "classic": ("Acme Video", 19.99, "USD"),
    "maze": ("StreamPlus", 17.99, "USD"),
    "trick": ("FitNow", 19.99, "USD"),
    "pause_trap": ("TuneBox", 14.99, "USD"),
    "buried": ("CineMax", 12.99, "USD"),
}
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}
ACCOUNT_KEYS = ("merchant", "price", "currency")


def _money(amount: float, currency: str) -> str:
    symbol = SYMBOLS.get(currency)
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency}"


def _account(scenario: str) -> tuple[str, float, str]:
    merchant, price, currency = DEFAULT_ACCOUNTS[scenario]
    given = {key: request.args.get(key) or request.cookies.get(f"velo_{key}") for key in ACCOUNT_KEYS}
    try:
        price = float(given["price"]) if given["price"] else price
    except ValueError:
        pass
    return (given["merchant"] or merchant)[:60], price, (given["currency"] or currency).upper()[:3]


def _render(scenario: str, template: str, **extra):
    merchant, price, currency = _account(scenario)
    response = make_response(render_template_string(
        template, brand=merchant, price=_money(price, currency), half=_money(round(price / 2, 2), currency),
        basic=_money(round(price / 3, 2), currency), upgrade=_money(round(price * 1.4, 2), currency), **extra))
    if any(request.args.get(key) for key in ACCOUNT_KEYS):
        for key in ACCOUNT_KEYS:  # remember for the next pages of this session
            if request.args.get(key):
                response.set_cookie(f"velo_{key}", request.args[key], samesite="Lax")
    return response


# ---------------------------------------------------------------------------------------------------
# Scenario "classic" (the original flow at /): hidden link, retention modal, final confirmation.
# ---------------------------------------------------------------------------------------------------

ACCOUNT = """<main>
  <h1>{{ brand }} membership</h1>
  <p>Plan: Premium monthly</p><p>Next charge: {{ price }}</p>
  <a id="cancel-link" href="{{ url_for('retention') }}">Need to cancel?</a>
</main>
"""

PAGE = "<!doctype html><title>{{ brand }} Account</title>" + STYLE + ACCOUNT

# The retention offer is a modal laid over the account page.
RETENTION = "<!doctype html><title>Stay with us</title>" + STYLE + ACCOUNT + """
<div class="modal"><div class="dialog">
<h2>Before you go...</h2><p>Keep Premium for only {{ half }}/month.</p>
<button onclick="location.href='{{ url_for('account') }}'">Keep my membership</button>
<button class="secondary" onclick="location.href='{{ url_for('confirm_cancel') }}'">Continue to cancellation</button>
</div></div>
"""

CONFIRM = "<!doctype html><title>Confirm cancellation</title>" + STYLE + """<main><h1>One last confirmation</h1>
<p>Your {{ brand }} benefits remain active until the end of the period.</p>
<a href="{{ url_for('cancelled') }}">Confirm cancellation</a>
<a href="{{ url_for('account') }}">Go back</a></main>
"""

DONE = ("<!doctype html><title>Cancelled</title>" + STYLE + "<main><h1>Cancellation requested</h1>"
        "<p>{{ brand }} will not charge you again.</p>" + CANCELLED + "</main>")


@app.get("/")
def account():
    return _render("classic", PAGE)


@app.get("/retention")
def retention():
    return _render("classic", RETENTION)


@app.get("/confirm-cancel")
def confirm_cancel():
    return _render("classic", CONFIRM)


@app.get("/cancelled")
def cancelled():
    return _render("classic", DONE)


# ---------------------------------------------------------------------------------------------------
# Harder scenarios at /s/<scenario>/<page>. In the templates, {{ go('page') }} is that page's URL.
# Wrong turns are deliberate: dead ends, loops back to the start, and pages that look finished but are not.
# ---------------------------------------------------------------------------------------------------

def _page(title: str, body: str) -> str:
    return f"<!doctype html><title>{title}</title>{STYLE}<main>{body}</main>"


def _link(page: str, text: str, css: str = "") -> str:
    return f"<a class=\"{css}\" href=\"{{{{ go('{page}') }}}}\">{text}</a>"


def _button(page: str, text: str, css: str = "") -> str:
    return f"<button class=\"{css}\" onclick=\"location.href='{{{{ go('{page}') }}}}'\">{text}</button>"


DONE_PAGE = _page("Membership ended", "<h1>Membership ended</h1><p>{{ brand }} will not charge you again.</p>" + CANCELLED)

SCENARIOS: dict[str, dict] = {
    "maze": {
        "pattern": "Roach motel: cancellation buried three menus deep behind decoy navigation",
        "pages": {
            "start": _page("{{ brand }} account", "<h1>{{ brand }} account</h1><nav>"
                           + _button("help", "Help centre") + _button("offers", "Plans &amp; offers") + _button("settings", "Settings")
                           + "</nav><p>Plan: Premium · Next charge {{ price }}</p>"),
            "help": _page("Help centre", "<h1>Help centre</h1><h2>How do I cancel?</h2>"
                          "<p>Memberships are managed by our support team. Contact us and we will get back to you.</p>"
                          + _button("support", "Contact support")),
            "support": _page("Support request", "<h1>Request received</h1><p>Our team will reply within 5–7 business days.</p>"),
            "offers": _page("Plans &amp; offers", "<h1>Plans &amp; offers</h1><div class=card><h2>Ultra</h2><p>4K and 6 screens.</p>"
                            + _button("upgraded", "Upgrade to Ultra", "primary") + "</div>"),
            "upgraded": _page("Plan upgraded", "<h1>You're on Ultra now!</h1><p>Your next charge is {{ upgrade }}.</p>"
                              + _button("start", "Back to account")),
            "settings": _page("Settings", "<h1>Settings</h1>" + _button("profile", "Profile") + _button("notifications", "Notifications")
                              + _button("billing", "Billing &amp; membership")),
            "profile": _page("Profile", "<h1>Profile</h1><p>Display name: Sam</p><button onclick=\"void 0\">Save changes</button>"),
            "notifications": _page("Notifications", "<h1>Notifications</h1><p>Email updates are on.</p>"),
            "billing": _page("Billing &amp; membership", "<h1>Billing &amp; membership</h1>" + _button("payment", "Update payment method")
                             + _button("history", "Billing history") + "<p>" + _link("manage", "Manage membership", "tiny") + "</p>"),
            "payment": _page("Payment method", "<h1>Payment method</h1><p>Visa ending 4242.</p>"),
            "history": _page("Billing history", "<h1>Billing history</h1><p>Last charge — {{ price }}</p><p>Previous charge — {{ price }}</p>"),
            "manage": _page("Manage membership", "<h1>Manage your {{ brand }} membership</h1>" + _button("start", "Keep my membership", "primary")
                            + "<p>" + _link("confirm", "End membership", "tiny") + "</p>"),
            "confirm": _page("Confirm", "<h1>End your {{ brand }} membership?</h1><p>You will lose access at the end of the period.</p>"
                             + _button("done", "Confirm end of membership")),
            "done": DONE_PAGE,
        },
    },
    "trick": {
        "pattern": "Confirmshaming discount offer, then a 'Cancel' button that keeps the membership",
        "pages": {
            "start": _page("{{ brand }} account", "<h1>{{ brand }} membership</h1><p>Plan: Pro · {{ price }}/month</p>"
                           + _button("offer", "Cancel membership", "secondary")),
            "offer": _page("Wait!", "<h1>Wait! Don't go yet</h1><div class=card><h2>Get 50% off for 3 months</h2>"
                           + _button("discount", "Claim 50% off", "primary") + "</div><p>"
                           + _link("confirm", "No thanks, I prefer paying full price", "tiny") + "</p>"),
            "discount": _page("Discount applied", "<h1>Discount applied!</h1><p>You'll pay {{ half }}/month for the next 3 months.</p>"
                              + _button("start", "Back to account")),
            "confirm": _page("Are you sure?", "<h1>Are you sure you want to end your {{ brand }} membership?</h1>"
                             + _button("kept", "Cancel", "primary") + _button("done", "Yes, end membership", "secondary")),
            "kept": _page("{{ brand }} account", "<h1>{{ brand }} membership</h1><p class=banner>Phew! Your membership continues.</p>"
                          "<p>Plan: Pro · {{ price }}/month</p>" + _button("offer", "Cancel membership", "secondary")),
            "done": DONE_PAGE,
        },
    },
    "pause_trap": {
        "pattern": "Pause and downgrade offered in place of cancelling, then a survey that blocks Continue",
        "pages": {
            "start": _page("{{ brand }} membership", "<h1>{{ brand }} membership</h1><p>Family plan · {{ price }}/month</p>"
                           + _link("options", "Cancel or change plan", "tiny")),
            "options": _page("Your options", "<h1>Before you cancel, consider:</h1>"
                             "<div class=card><h2>Take a break</h2>" + _button("paused", "Pause for 3 months", "primary") + "</div>"
                             "<div class=card><h2>Pay less</h2>" + _button("downgraded", "Switch to Basic – {{ basic }}", "primary") + "</div>"
                             "<p>" + _link("survey", "I still want to cancel", "tiny") + "</p>"),
            "paused": _page("Membership paused", "<h1>Your {{ brand }} membership is paused</h1>"
                            "<p>Billing resumes automatically on 1 Feb 2027.</p>" + _button("start", "Back to membership")),
            "downgraded": _page("Plan changed", "<h1>Plan changed to Basic</h1><p>You'll be charged {{ basic }}/month from your next bill.</p>"
                                + _button("start", "Back to membership")),
            "survey": _page("Tell us why", "<h1>Tell us why you're leaving {{ brand }}</h1>"
                            "<label><input type=radio name=reason onchange=\"document.getElementById('go').disabled=false\"> Too expensive</label><br>"
                            "<label><input type=radio name=reason onchange=\"document.getElementById('go').disabled=false\"> Not using it</label><br>"
                            "<button id=go disabled onclick=\"location.href='{{ go('final') }}'\">Continue</button>"),
            "final": _page("Finish cancelling", "<h1>Last step</h1>" + _button("done", "Finish cancellation")),
            "done": DONE_PAGE,
        },
    },
    "buried": {
        "pattern": "Cancel link hidden in the footer below long promotional content",
        "pages": {
            "start": _page("{{ brand }} home", "<h1>{{ brand }}</h1>"
                           + "".join(f"<div class=promo><h2>{title}</h2>" + _button("catalog", "Explore more shows") + "</div>"
                                     for title in ("New this week", "Trending now", "Because you watched"))
                           + "<footer>" + _link("privacy", "Privacy") + _link("terms", "Terms") + _link("confirm", "Cancel membership")
                           + "</footer>"),
            "catalog": _page("Catalog", "<h1>Catalog</h1><button onclick=\"void 0\">Play trailer</button>"),
            "privacy": _page("Privacy", "<h1>Privacy policy</h1><p>We value your privacy.</p>"),
            "terms": _page("Terms", "<h1>Terms of use</h1><p>By using {{ brand }} you agree to these terms.</p>"),
            "confirm": _page("Cancel {{ brand }}", "<h1>Cancel your {{ brand }} membership?</h1><p>Current plan: {{ price }}/month</p>"
                             + _button("done", "Confirm cancellation")),
            "done": DONE_PAGE,
        },
    },
}


@app.get("/s/<scenario>/")
@app.get("/s/<scenario>/<page>")
def scenario_page(scenario: str, page: str = "start"):
    pages = SCENARIOS.get(scenario, {}).get("pages", {})
    if page not in pages:
        abort(404)
    return _render(scenario, pages[page], go=lambda target: url_for("scenario_page", scenario=scenario, page=target))


if __name__ == "__main__":
    # Loopback only; the port follows MOCK_SITE_URL so the agent's origin check matches.
    app.run(host="127.0.0.1", port=urlparse(settings.mock_site_url).port or 5000, debug=False)
