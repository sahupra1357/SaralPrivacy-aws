"""Payload builders shared by the assessments tests."""

from typing import Any

import pytest

GENERAL_RESULT: dict[str, Any] = {
    "rawScore": 41,
    "finalScore": 59,
    "verdictBand": "Building Foundations",
    "verdictDescription": "Basic elements exist but are not applied consistently.",
    "dataExposure": 62,
    "controlMaturity": 48,
    "operationalReadiness": 55,
    "categoryScores": {
        "noticeConsent": 70,
        "accessControl": 40,
        "retentionDeletion": 30,
        "ownershipGovernance": 55,
        "vendorPartnerRisk": 45,
        "incidentReadiness": 20,
    },
    "redFlagsTriggered": ["No consent record kept"],
    "immediateActions": ["Publish a privacy notice", "Name a grievance officer"],
    "thirtyDayActions": ["Build a data inventory"],
}


@pytest.fixture
def general_payload() -> dict[str, Any]:
    return {
        "email": "Priya@example.com",
        "name": "Priya",
        "business": "Rangoli Retail",
        "mobile": "9876500000",
        "report_type": "quick",
        "answers": {
            "q1_sector": "retail",
            "q4_data_types": ["name", "phone"],
            "q11_blocker": "budget",
            "q12_resource": "none",
        },
        "result": GENERAL_RESULT,
        "consentReport": False,
        "consentNewsletter": False,
        "consentFollowup": False,
        "answerSummary": [
            {"question": "Which sector are you in?", "answer": "Retail"},
            {"question": "What data do you collect?", "answer": "Name, Phone"},
            {"question": "Do you keep consent records?", "answer": "No"},
            {"question": "Who owns privacy?", "answer": "Nobody yet"},
        ],
    }


@pytest.fixture
def industry_payload() -> dict[str, Any]:
    return {
        "email": "ca@example.com",
        "name": "Anand",
        "business": "Anand & Co",
        "mobile": "9876500001",
        "industry": "ca-firms",
        "report_type": "ca-firm",
        "answers": {"q0": "a1", "q1": "b2"},
        "result": {
            "finalScore": 72,
            "rawScore": 28,
            "verdictBand": "Moderate Risk",
            "verdictDescription": "Controls exist but are uneven.",
            "dataExposure": 30,
            "controlMaturity": 70,
            "operationalReadiness": 0,
            "categoryScores": {"clientData": 40, "staffAccess": 20},
            "redFlagsTriggered": [],
            "immediateActions": ["Lock down the shared drive"],
            "thirtyDayActions": [],
        },
        "consentReport": True,
        "consentNewsletter": False,
        "consentFollowup": False,
    }
