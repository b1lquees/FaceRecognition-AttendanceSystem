"""Checks on the stylesheet and the templates that use it.

The suite has been blind to layout twice. The camera error overlay was visible from page
load because `.video-error { display: grid }` beat the `hidden` attribute, and a page once
referenced a `.card-body` class that did not exist. Both rendered a 200 and both passed
every test in this project.

A browser is the only way to know something *looks* right, and that is not what these do.
They check the things that are true or false regardless of rendering: that a class used in
a template exists in the stylesheet, that a custom property used is defined, that the two
theme blocks define the same tokens, and that the one rule protecting `hidden` is present.
Cheap, exact, and enough to catch the two bugs that actually happened.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "attendance" / "static"
TEMPLATES = Path(__file__).resolve().parent.parent / "attendance" / "templates"

CSS = (STATIC / "style.css").read_text(encoding="utf-8")
TEMPLATE_FILES = sorted(TEMPLATES.glob("*.html"))

# stripped before anything else: a class name mentioned inside a comment is not a rule,
# and neither is one inside a jinja comment in a template
CSS_WITHOUT_COMMENTS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)

# Classes that exist in the markup but deliberately have no rule of their own. Every entry
# is a decision, not an exemption -- if this list starts growing, the naming has drifted.
UNSTYLED_CLASSES = set()

# Tokens that live in :root only, with no dark-mode override, because they are not
# colours. A radius is the same in both themes; overriding it would be noise. Anything
# colour-like missing from the dark block is a bug, which is what the test below checks.
LIGHT_ONLY_TOKENS = {"--radius", "--radius-sm", "--sidebar"}


def css_class_names():
    """Every class name the stylesheet defines a rule for."""
    return set(re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", CSS_WITHOUT_COMMENTS))


def template_class_names(text):
    """Class names from attributes that are entirely literal.

    Attributes containing Jinja are skipped rather than guessed at, and that restraint is
    the point. A first attempt tried to extract the literals out of expressions and was
    wrong in two ways at once: `class="{{ 'active' if active_page == 'today' }}"` yielded
    `today`, which is a value being compared and not a class at all, and
    `class="flash flash-{{ category }}"` yielded `flash-`, half of a name assembled at
    runtime. Both produced confident failures about classes that were never referenced.

    Skipping them costs little. The bug this test exists to catch -- a page asking for
    `.card-body`, which did not exist -- was a plain static attribute.
    """
    names = set()
    for value in re.findall(r'class="([^"]*)"', text):
        if "{{" in value or "{%" in value:
            continue  # assembled at runtime; not knowable from here
        names.update(value.split())
    return names


# --- the rule that was missed ------------------------------------------------------

# The regression guard for the camera overlay. [hidden] { display: none } is a user-agent
# rule, and any author rule setting display beats it, so a class with `display` silently
# defeats the attribute. This normalisation is what puts the attribute back in charge, and
# deleting it would reintroduce the bug invisibly.
def test_the_hidden_attribute_is_normalised():
    match = re.search(r"\[hidden\]\s*\{([^}]*)\}", CSS_WITHOUT_COMMENTS)

    assert match, "no [hidden] rule -- an element with the hidden attribute may still show"
    body = match.group(1).replace(" ", "")
    assert "display:none!important" in body, (
        "[hidden] must use !important, or any class that sets display will override it"
    )


# --- classes referenced but never defined ------------------------------------------

# This is the test that would have caught .card-body: the page rendered, returned 200, and
# had no padding, because the class it asked for did not exist.
@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: p.name)
def test_every_class_a_template_uses_exists_in_the_stylesheet(path):
    used = template_class_names(path.read_text(encoding="utf-8"))
    defined = css_class_names()

    missing = sorted(used - defined - UNSTYLED_CLASSES)

    assert not missing, (
        f"{path.name} uses classes with no rule in style.css: {missing}. "
        "Either add the rule or fix the name -- the page will render either way, which is "
        "why this is a test and not something you would notice."
    )


# --- custom properties -------------------------------------------------------------

def defined_tokens(block):
    return set(re.findall(r"(--[A-Za-z0-9-]+)\s*:", block))


def test_every_custom_property_used_is_defined():
    used = set(re.findall(r"var\((--[A-Za-z0-9-]+)", CSS_WITHOUT_COMMENTS))
    defined = defined_tokens(CSS_WITHOUT_COMMENTS)

    missing = sorted(used - defined)

    assert not missing, (
        f"style.css uses undefined custom properties: {missing}. "
        "An undefined var() silently resolves to nothing, so the affected rule just "
        "disappears rather than erroring."
    )


# The palette lives in three blocks, not two: light, dark-because-the-system-says-so, and
# dark-because-the-theme-switch-says-so. The last two are the same list of values written
# out twice, which is exactly the arrangement that drifts -- somebody tunes a colour, sees
# it change, and never learns that they only fixed one of the two ways to be in dark mode.
DARK_BLOCK_PATTERNS = {
    "the prefers-color-scheme block": (
        r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root[^{]*\{([^}]*)\}"
    ),
    'the [data-theme="dark"] block': r':root\[data-theme="dark"\]\s*\{([^}]*)\}',
}


@pytest.mark.parametrize("name, pattern", DARK_BLOCK_PATTERNS.items(), ids=lambda v: str(v)[:24])
def test_each_dark_block_defines_the_same_tokens_as_light(name, pattern):
    # the bare `:root {` is the light block; the dark ones both qualify it, with a
    # :not() or an attribute, so neither can match this
    light = re.search(r":root\s*\{([^}]*)\}", CSS_WITHOUT_COMMENTS)
    dark = re.search(pattern, CSS_WITHOUT_COMMENTS)

    assert light, "expected a bare :root block holding the light palette"
    assert dark, f"expected {name} -- without it that route into dark mode has no palette"

    light_tokens = defined_tokens(light.group(1))
    dark_tokens = defined_tokens(dark.group(1))

    # dark must not introduce a token light does not have -- that is a typo, and the
    # misspelled name silently resolves to nothing in dark mode only
    assert not (dark_tokens - light_tokens), (
        f"defined only in {name}: {sorted(dark_tokens - light_tokens)}. "
        "A name that exists in one theme and not the other is almost always a typo."
    )

    # and every light token except the documented non-colour ones needs an override
    missing = sorted(light_tokens - dark_tokens - LIGHT_ONLY_TOKENS)
    assert not missing, (
        f"no value in {name} for: {missing}. The rule using it keeps its light colour "
        "in dark mode, which is the kind of thing only a human looking at the page sees."
    )


# Choosing light on a machine set to dark is the whole point of the switch, and it only
# works because the media query excludes that case. Without the :not() the media query and
# the attribute both match, the media query is later in the file, and the button appears
# to do nothing at all in one direction.
def test_the_system_dark_rule_yields_to_an_explicit_light_choice():
    dark_media = re.search(
        r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*(:root[^{]*)\{",
        CSS_WITHOUT_COMMENTS,
    )

    assert dark_media, "expected a prefers-color-scheme: dark block"
    assert ':not([data-theme="light"])' in dark_media.group(1), (
        "the system dark rule must exclude an explicit light choice, or the theme switch "
        "cannot turn dark mode off on a machine whose system theme is dark."
    )


# --- the stylesheet is actually reachable ------------------------------------------

def test_the_stylesheet_is_served(client, login):
    login()

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert response.mimetype == "text/css"


# every page links it through url_for, so a page that forgot would render unstyled
def test_every_page_inherits_the_stylesheet_link():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "style.css" in base

    for path in TEMPLATE_FILES:
        if path.name == "base.html":
            continue
        text = path.read_text(encoding="utf-8")
        assert 'extends "base.html"' in text, (
            f"{path.name} does not extend base.html, so it gets no stylesheet, no nav "
            "and no flash messages"
        )
