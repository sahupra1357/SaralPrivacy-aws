"""The notice document must match `frontend/lib/notice-pack/render.ts` exactly — the
browser renders the same document for Copy HTML and the print fallback.
"""

from datetime import UTC, datetime

from app.notice_pack import render
from app.notice_pack.state import NoticeState


def _state(**kw: object) -> NoticeState:
    return NoticeState.model_validate(kw)


EFF = datetime(2026, 9, 18, 6, 30, tzinfo=UTC)


# ── escHtml / slugify ────────────────────────────────────────────────────────
def test_esc_html_escapes_amp_quote_and_lt_but_not_gt() -> None:
    assert render.esc_html('A & B "c" <d> >') == "A &amp; B &quot;c&quot; &lt;d> >"


def test_esc_html_treats_none_as_empty() -> None:
    assert render.esc_html(None) == ""


def test_slugify_collapses_punctuation_and_trims_dashes() -> None:
    assert render.slugify("  Acme Pvt. Ltd! ") == "acme-pvt-ltd"


def test_slug_for_file_falls_back_and_caps_at_60_chars() -> None:
    assert render.slug_for_file("") == "your-business"
    assert render.slug_for_file("!!!") == "your-business"
    assert len(render.slug_for_file("x" * 200)) == 60


# ── effective date ───────────────────────────────────────────────────────────
def test_effective_date_is_english_day_month_year() -> None:
    html = render.build_notice(_state(org="Acme"), "en", EFF)
    assert "Effective date: 18 September 2026" in html


def test_effective_date_is_hindi_month_name_in_hi() -> None:
    html = render.build_notice(_state(org="Acme", lang="hi"), "hi", EFF)
    assert "प्रभावी तिथि: 18 सितंबर 2026" in html


# ── §1 who we are ────────────────────────────────────────────────────────────
def test_who_we_are_uses_placeholder_when_org_blank() -> None:
    html = render.build_notice(_state(), "en", EFF)
    assert '<h2 class="dh">1. Who we are</h2><p>[business].</p>' in html


def test_who_we_are_adds_sector_website_address_and_officer() -> None:
    html = render.build_notice(
        _state(
            org="Acme",
            sector="ca",
            website="acme.in",
            regAddress="12 MG Road",
            cName="R Rao",
            cEmail="dpo@acme.in",
        ),
        "en",
        EFF,
    )
    assert (
        "<p>Acme is a CA Firm. Website: acme.in. Registered address: 12 MG Road."
        " For privacy matters, contact R Rao at dpo@acme.in.</p>" in html
    )


def test_who_we_are_falls_back_to_grievance_officer_when_only_email_given() -> None:
    html = render.build_notice(_state(org="Acme", cEmail="dpo@acme.in"), "en", EFF)
    assert "contact our Grievance Officer at dpo@acme.in." in html


# ── §2/§3 data and purposes ──────────────────────────────────────────────────
def test_empty_data_renders_the_two_muted_placeholders() -> None:
    html = render.build_notice(_state(org="Acme"), "en", EFF)
    assert '<p class="muted">— not specified yet —</p>' in html
    assert '<p class="muted">— add purposes —</p>' in html


def test_purpose_falls_back_to_ellipsis_for_unmapped_data() -> None:
    html = render.build_notice(
        _state(org="Acme", data=["PAN", "Email"], purpose={"PAN": "Tax filing"}), "en", EFF
    )
    assert "<li><b>PAN</b> — Tax filing</li><li><b>Email</b> — …</li>" in html


# ── §4 collection contexts ───────────────────────────────────────────────────
def test_context_keys_render_as_labels_and_unknown_keys_pass_through() -> None:
    html = render.build_notice(_state(org="Acme", contexts=["checkout", "mystery"]), "en", EFF)
    assert "<li>Checkout / order page</li><li>mystery</li>" in html


# ── §5 sharing ───────────────────────────────────────────────────────────────
def test_no_vendors_flag_wins_over_a_vendor_list() -> None:
    html = render.build_notice(_state(org="Acme", noVendors=True, vendors=["CRM"]), "en", EFF)
    assert f"<p>{render.NO_SHARING_CLAUSE}</p>" in html


def test_empty_vendor_list_uses_the_sector_vendor_clause() -> None:
    html = render.build_notice(_state(org="Acme", sector="clinic"), "en", EFF)
    assert "We may share patient data with diagnostic labs" in html


def test_unknown_sector_uses_the_generic_vendor_clause() -> None:
    html = render.build_notice(_state(org="Acme", sector="nope"), "en", EFF)
    assert "We may share personal data with service providers such as payment gateways" in html


def test_vendor_list_renders_the_structured_purpose_list() -> None:
    html = render.build_notice(
        _state(org="Acme", vendors=["Payment gateway", "Mystery Co"]), "en", EFF
    )
    assert "<li><b>Payment gateway</b> — to process payments and refunds</li>" in html
    assert "<li><b>Mystery Co</b> — to deliver the services described in this notice</li>" in html


# ── §6 children ──────────────────────────────────────────────────────────────
def test_children_section_appears_only_when_children_is_yes() -> None:
    assert "6. Children’s data" not in render.build_notice(
        _state(org="A", children="No"), "en", EFF
    )
    assert "6. Children’s data" in render.build_notice(_state(org="A", children="Yes"), "en", EFF)


# ── §7 retention / §8 withdrawal ─────────────────────────────────────────────
def test_known_retention_choice_maps_to_its_phrase() -> None:
    html = render.build_notice(_state(org="A", retention="3 years"), "en", EFF)
    assert "<p>We keep your data for up to 3 years.</p>" in html


def test_weak_retention_keeps_the_bracketed_placeholder() -> None:
    html = render.build_notice(_state(org="A", retention="Not sure"), "en", EFF)
    assert "<p>We keep your data [set a retention period].</p>" in html


def test_unknown_retention_uses_the_fallback_phrase() -> None:
    html = render.build_notice(_state(org="A", retention="whenever"), "en", EFF)
    assert "for as long as needed for the purposes above" in html


def test_withdrawal_contact_falls_back_to_the_grievance_email() -> None:
    html = render.build_notice(_state(org="A", cEmail="dpo@a.in"), "en", EFF)
    assert "You can withdraw consent at any time — dpo@a.in.</p>" in html


def test_withdraw_contact_overrides_the_grievance_email() -> None:
    html = render.build_notice(
        _state(org="A", cEmail="dpo@a.in", withdrawContact="opt-out@a.in"), "en", EFF
    )
    assert "at any time — opt-out@a.in.</p>" in html


# ── §10/§11 officer placeholders ─────────────────────────────────────────────
def test_officer_and_name_placeholders_when_contact_is_blank() -> None:
    html = render.build_notice(_state(org="A"), "en", EFF)
    assert "<p>[Officer]. If we do not resolve your grievance" in html
    assert '<h2 class="dh">11. Contact us</h2><p>[name]</p>' in html


def test_contact_line_joins_name_email_and_phone() -> None:
    html = render.build_notice(
        _state(org="A", cName="R Rao", cEmail="d@a.in", cPhone="+91 90000 00000"), "en", EFF
    )
    assert "<p>R Rao · d@a.in · +91 90000 00000</p>" in html


# ── document shell ───────────────────────────────────────────────────────────
def test_document_shell_carries_lang_title_and_inline_css() -> None:
    html = render.notice_document_html(_state(org="Acme", lang="hi"), EFF)
    assert html.startswith('<!doctype html><html lang="hi">')
    assert "<title>Privacy Notice — Acme</title>" in html
    assert "@page{size:A4;margin:18mm 16mm 20mm}" in html
    assert html.endswith("</div></body></html>")


def test_document_title_escapes_the_business_name() -> None:
    html = render.notice_document_html(_state(org='A<b>&"'), EFF)
    assert "<title>Privacy Notice — A&lt;b>&amp;&quot;</title>" in html


def test_document_title_falls_back_to_your_business() -> None:
    assert "<title>Privacy Notice — Your business</title>" in render.notice_document_html(
        _state(), EFF
    )


def test_hindi_document_uses_hindi_headings_and_disclaimer() -> None:
    html = render.notice_document_html(_state(org="एक्मे", lang="hi"), EFF)
    assert "1. हम कौन हैं" in html
    assert "यह सूचना मार्गदर्शन हेतु है, कानूनी सलाह नहीं। प्रकाशित करने से पहले समीक्षा करें।" in html


def test_pdf_header_and_footer_carry_the_brand_and_page_counter() -> None:
    assert "DPDPA Privacy Notice" in render.PDF_HEADER
    assert "Generated by SaralPrivacy — a practical draft, not legal advice." in render.PDF_FOOTER
    assert 'class="pageNumber"' in render.PDF_FOOTER
    assert render.PDF_MARGINS == {"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"}
