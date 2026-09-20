"""Sector / context / vendor tables used by the notice document.

Copied verbatim from `frontend/lib/notice-pack/data.ts` and the `VENDOR_PURPOSE` map in
`frontend/lib/notice-pack/engine.ts`. DPDPA-facing wording is never rewritten: if the
TypeScript changes, change it here too.

Only the fields the renderer reads are kept. `SECTORS` here is `key -> (label, vclause)`;
the wizard's per-sector `data` / `purposes` / `vendors` / `contexts` / `children` lists
drive the UI only and stay in TypeScript.
"""

from typing import Final

# CONTEXTS: collection-point key -> label (the `mini(...)` snippets are client-only).
CONTEXT_LABELS: Final[dict[str, str]] = {
    "contact": "Website contact form",
    "whatsapp": "WhatsApp enquiry",
    "checkout": "Checkout / order page",
    "appointment": "Appointment booking",
    "admission": "Admission form",
    "job": "Job application form",
    "onboarding": "Client onboarding form",
    "support": "Customer support form",
    "newsletter": "Newsletter signup",
    "feedback": "Feedback / review form",
    "offline": "Offline paper form",
    "app": "Mobile app",
    "cctv": "CCTV / visitor entry",
    "employee": "Employee / contractor onboarding",
}

# SECTORS: key -> proper-cased label used in "§1 Who we are".
SECTOR_LABELS: Final[dict[str, str]] = {
    "ca": "CA Firm",
    "recruitment": "Recruitment / Staffing",
    "coaching": "Coaching / Training Institute",
    "d2c": "D2C Brand",
    "clinic": "Clinic / Diagnostic Lab",
    "school": "School / College",
    "law": "Law Firm",
    "realestate": "Real Estate Broker",
    "hotel": "Hotel / Travel",
    "pharmacy": "Pharmacy",
    "fintech": "Financial Services / NBFC",
    "manufacturing": "Manufacturing / Industrial",
}

# SECTORS: key -> which VENDOR_CLAUSE the sector falls back to.
SECTOR_VCLAUSE: Final[dict[str, str]] = {
    "ca": "ca",
    "recruitment": "generic",
    "coaching": "generic",
    "d2c": "d2c",
    "clinic": "clinic",
    "school": "generic",
    "law": "generic",
    "realestate": "generic",
    "hotel": "generic",
    "pharmacy": "clinic",
    "fintech": "generic",
    "manufacturing": "generic",
}

VENDOR_CLAUSE: Final[dict[str, str]] = {
    "generic": (
        "We may share personal data with service providers such as payment gateways, "
        "logistics partners, communication tools, cloud storage providers, CRM systems, "
        "professional advisors or other vendors, only for the purposes described in this notice."
    ),
    "d2c": (
        "We may share order and contact details with payment gateways, delivery partners, "
        "customer-support tools, communication providers and analytics platforms."
    ),
    "ca": (
        "We may share client data with authorised tax portals, accounting systems, audit tools, "
        "cloud storage providers and professional advisors where required for service delivery "
        "or legal compliance."
    ),
    "clinic": (
        "We may share patient data with diagnostic labs, pharmacies, insurance partners, "
        "cloud software providers and communication tools where required for medical service delivery."
    ),
}

# Why each recipient category receives data — drives the structured sharing list (§5).
VENDOR_PURPOSE: Final[dict[str, str]] = {
    "Payment gateway": "to process payments and refunds",
    "Logistics / courier": "to deliver orders and manage returns",
    "WhatsApp / SMS / email provider": "to send service updates and, with consent, marketing messages",
    "Cloud storage": "to host and back up records securely",
    "CRM": "to manage customer records and communication",
    "Accounting / tax consultant": "for accounting, tax filing and statutory compliance",
    "Legal consultant": "for legal advice and compliance",
    "HR / payroll software": "to run payroll and HR administration",
    "Recruitment / BGV vendor": "for candidate sourcing and background verification",
    "LMS / EdTech platform": "to deliver classes and learning",
    "Diagnostic lab partner": "to perform tests and deliver reports",
    "Insurance partner": "to process claims and billing",
    "Hotel / travel platform": "to manage bookings and travel",
    "Analytics / advertising": "to measure usage and, with consent, run marketing",
    "CCTV / security vendor": "for premises safety, security and access control",
}

# retText(): retention choice -> the phrase that follows "We keep your data ".
RETENTION_TEXT: Final[dict[str, str]] = {
    "Until service ends": "for as long as needed to provide the service",
    "6 months": "for up to 6 months after your last activity",
    "1 year": "for up to 1 year after your last activity",
    "3 years": "for up to 3 years",
    "5–8 yrs (tax/legal)": "for 5–8 years to meet tax and legal requirements",
    "As required by law": "for as long as applicable law requires",
    "Forever": "[set a retention period]",
    "Not sure": "[set a retention period]",
}
RETENTION_FALLBACK: Final[str] = "for as long as needed for the purposes above"

# Month names as Intl.DateTimeFormat("en-IN" | "hi-IN", {month:"long"}) renders them,
# so the effective date reads exactly as it did on Vercel.
MONTHS_EN: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTHS_HI: Final[tuple[str, ...]] = (
    "जनवरी",
    "फ़रवरी",
    "मार्च",
    "अप्रैल",
    "मई",
    "जून",
    "जुलाई",
    "अगस्त",
    "सितंबर",
    "अक्तूबर",
    "नवंबर",
    "दिसंबर",
)
