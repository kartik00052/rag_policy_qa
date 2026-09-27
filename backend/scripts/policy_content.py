"""Shared content for the sample policy fixture documents.

Kept separate from the generators so the DOCX and PDF samples contain identical
facts, which makes cross-format verification meaningful.

Tables are attached to the section they belong to (a real policy puts a
reimbursement table inside its reimbursement section, not in an appendix), so
chunk metadata like ``section`` / ``heading_path`` is genuinely testable.
"""

from __future__ import annotations

Table = tuple[str, list[str], list[list[str]]]
Section = tuple[str, list[str], list[Table]]

TITLE = "Acme Global Travel & Leave Policy"

SECTIONS: list[Section] = [
    (
        "1. Purpose and Scope",
        [
            "This policy governs business travel, leave entitlements and expense "
            "reimbursement for all Acme Global employees. It applies to full-time "
            "and part-time staff on permanent contracts. Contractors are governed "
            "by the Contractor Engagement Policy instead.",
            "This document supersedes the 2024 edition of the travel policy. Where "
            "this document conflicts with a local statutory requirement, the local "
            "requirement takes precedence.",
        ],
        [],
    ),
    (
        "2. Leave Entitlements",
        [
            "Annual leave accrues at 1.75 days per completed calendar month, "
            "capped at 25 days per leave year. Accrual begins on the first day of "
            "employment.",
        ],
        [],
    ),
    (
        "2.1 Eligibility for Parental Leave",
        [
            "Employees who have completed six months of continuous service are "
            "eligible to take paid parental leave. The six month qualifying period "
            "is waived for employees returning from a documented medical leave.",
            "Parental leave is capped at 18 weeks for the primary caregiver and 6 "
            "weeks for the secondary caregiver. A maximum of 3 separate periods "
            "may be taken within 24 months of the child's birth.",
        ],
        [],
    ),
    (
        "2.2 Sick Leave",
        [
            "Full-time employees accrue 10 days of paid sick leave per leave year. "
            "Sick leave may be carried forward into the next leave year up to a "
            "maximum of 5 unused days.",
        ],
        [
            (
                "Table 2.2a: Leave accrual rates",
                ["Leave type", "Accrual rate", "Annual cap (days)"],
                [
                    ["Annual leave", "1.75 days per month", "25"],
                    ["Sick leave", "10 days per year", "10"],
                    ["Parental leave", "18 weeks primary caregiver", "90"],
                ],
            )
        ],
    ),
    (
        "6. Travel and Expense Reimbursement",
        [
            "All business travel must be pre-approved by the employee's line "
            "manager in the Workday travel module before any costs are incurred. "
            "Unapproved expenses will be declined at claim stage.",
        ],
        [],
    ),
    (
        "6.1 Air Travel and Booking",
        [
            "Economy class must be booked for all flights under 6 hours. Premium "
            "economy is permitted for flights of 6 hours or more, subject to "
            "manager approval. Business class requires written director sign-off.",
            "Preferred airline alliance bookings should be used where a route is "
            "available. Hotel and car rental should be booked through the corporate "
            "portal to access negotiated rates.",
        ],
        [],
    ),
    (
        "6.2 Reimbursement Limits",
        [
            "Employees may claim up to 1500 USD per trip for accommodation. Claims "
            "above 1500 USD require written approval from a director before travel "
            "is booked.",
            "Claims must be submitted within 30 days of completing the trip. Claims "
            "submitted after 30 days are only reimbursed at the discretion of the "
            "Head of Finance, and receipts are mandatory for all claims.",
            "Meals are reimbursed at a flat daily allowance of 75 USD for domestic "
            "travel and 100 USD for international travel. Itemised meal receipts "
            "are not required.",
        ],
        [
            (
                "Table 6.2a: Per diem allowances by destination",
                ["Destination", "Daily allowance (USD)", "Currency"],
                [
                    ["New York, USA", "100", "USD"],
                    ["London, UK", "95", "GBP equivalent"],
                    ["Singapore", "85", "SGD equivalent"],
                    ["Remote / domestic day", "75", "USD"],
                ],
            ),
            (
                "Table 6.2b: Accommodation tiers by city band",
                ["City band", "Examples", "Nightly cap (USD)"],
                [
                    ["Band A", "New York, London, Tokyo", "1500"],
                    ["Band B", "Chicago, Berlin, Sydney", "900"],
                    ["Band C", "All other locations", "550"],
                ],
            ),
        ],
    ),
    (
        "6.3 Ground Transport",
        [
            "Rail travel is preferred for journeys under 400 kilometers. Taxi or "
            "rideshare fares are reimbursed in full with receipts, capped at 120 "
            "USD per day.",
        ],
        [],
    ),
    (
        "9. Compliance",
        [
            "This policy is reviewed annually by the People Operations team. "
            "Violations of the approval workflow may result in disciplinary action "
            "up to and including termination of employment.",
        ],
        [],
    ),
]

APPENDIX = (
    "9.1 Appendix A: Approval Workflow",
    [
        "Manager approval is required for all travel. Director approval is "
        "required for any single expense above 1500 USD. Finance approval is "
        "required for claims submitted outside the 30 day window.",
        "Approval requests must be submitted at least 5 working days before "
        "travel. Requests submitted fewer than 5 working days in advance are "
        "escalated to the Director for review.",
    ],
)

