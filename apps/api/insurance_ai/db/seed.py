"""Synthetic seed data. ALL RECORDS ARE FAKE — no real PII.

Covers every required scenario (spec §17): active auto, lapsed policy, open &
closed claims, payment due, past-due, autopay, pending renewal, multiple-policy
household, and a coverage dispute requiring escalation — across all seven products.

Demo verification: every customer verifies with policy_number + zip_code, or with
date_of_birth, or with the demo OTP 123456.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.db.models import (
    Appointment,
    Claim,
    Conversation,
    Coverage,
    Customer,
    InsuredAsset,
    Invoice,
    Message,
    Payment,
    Policy,
    SupportTicket,
    ToolExecution,
)
from insurance_ai.domain.enums import (
    ClaimStatus,
    PaymentStatus,
    PolicyStatus,
    ProductType,
)

TODAY = date.today()


def _d(days: int) -> date:
    return TODAY + timedelta(days=days)


async def _clear(db: AsyncSession) -> None:
    # Children first so foreign-key constraints are satisfied. Re-seeding is a full
    # reset of the synthetic dataset, including conversation history.
    for model in (
        Message,
        ToolExecution,
        Payment,
        Invoice,
        Appointment,
        SupportTicket,
        Claim,
        Coverage,
        InsuredAsset,
        Conversation,
        Policy,
        Customer,
    ):
        await db.execute(delete(model))
    await db.commit()


async def seed(db: AsyncSession) -> dict[str, int]:
    await _clear(db)
    counts = {"customers": 0, "policies": 0, "claims": 0, "invoices": 0}

    # ---- Customer 1: Maria Alvarez — multi-policy household, open claim --------
    maria = Customer(
        first_name="Maria",
        last_name="Alvarez",
        email="maria.alvarez@example-synth.test",
        phone="210-555-0142",
        date_of_birth=date(1985, 3, 12),
        zip_code="78258",
        address="1420 Oak Vista, San Antonio, TX 78258",
        comm_preference="email",
        notes="SYNTHETIC demo customer.",
    )
    db.add(maria)
    await db.flush()

    auto = Policy(
        policy_number="AUTO-10024",
        customer_id=maria.id,
        product_type=ProductType.AUTO,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-200),
        renewal_date=_d(40),
        premium_amount=142.50,
        billing_cadence="monthly",
        autopay=False,
        details={"drivers": ["Maria Alvarez", "Luis Alvarez"], "state": "TX"},
    )
    home = Policy(
        policy_number="HOME-20011",
        customer_id=maria.id,
        product_type=ProductType.HOMEOWNERS,
        status=PolicyStatus.PENDING_RENEWAL,
        effective_date=_d(-330),
        renewal_date=_d(20),
        premium_amount=98.00,
        billing_cadence="monthly",
        autopay=True,
        details={"dwelling": "1420 Oak Vista", "year_built": 2004},
    )
    db.add_all([auto, home])
    await db.flush()

    db.add_all(
        [
            Coverage(
                policy_id=auto.id,
                coverage_type="liability",
                limit_amount=100000,
                description="Bodily injury liability per person.",
            ),
            Coverage(
                policy_id=auto.id,
                coverage_type="collision",
                deductible=500,
                description="Collision coverage after deductible.",
            ),
            Coverage(
                policy_id=auto.id,
                coverage_type="comprehensive",
                deductible=250,
                exclusions=["intentional damage", "racing"],
                description="Comprehensive (theft, weather, glass).",
            ),
            Coverage(
                policy_id=auto.id,
                coverage_type="rental_reimbursement",
                limit_amount=40,
                per_unit="per day (max 30 days)",
                description="Rental reimbursement following a covered loss.",
            ),
            Coverage(
                policy_id=home.id,
                coverage_type="dwelling",
                limit_amount=320000,
                deductible=1000,
                exclusions=["flood", "earthquake"],
                description="Dwelling structure coverage.",
            ),
            Coverage(
                policy_id=home.id,
                coverage_type="personal_property",
                limit_amount=160000,
                deductible=1000,
                description="Personal property coverage.",
            ),
        ]
    )
    db.add_all(
        [
            InsuredAsset(
                policy_id=auto.id,
                asset_type="vehicle",
                description="2019 Honda CR-V",
                identifier="1HGCR2F powered-VIN-SYNTH",
                attributes={"year": 2019},
            ),
            InsuredAsset(
                policy_id=home.id,
                asset_type="dwelling",
                description="Single-family home",
                identifier="1420 Oak Vista",
                attributes={"sqft": 2100},
            ),
        ]
    )
    claim1 = Claim(
        claim_number="CLAIM-90001",
        policy_id=auto.id,
        customer_id=maria.id,
        status=ClaimStatus.UNDER_REVIEW,
        loss_type="collision",
        description="Rear-ended at a stoplight; rear bumper and tailgate damage.",
        date_of_loss=_d(-14),
        reported_date=_d(-13),
        adjuster_name="Dana Whitfield",
        adjuster_phone="210-555-0199",
        reserve_amount=4200,
        next_steps=["Adjuster inspection scheduled", "Upload repair estimate"],
        requested_documents=["Photos of damage", "Repair shop estimate"],
    )
    db.add(claim1)
    # Auto invoice: currently due (payment due scenario)
    inv_auto = Invoice(
        invoice_number="INV-AUTO-10024-07",
        policy_id=auto.id,
        amount_due=142.50,
        amount_paid=0,
        due_date=_d(6),
        status=PaymentStatus.PENDING,
        period_start=_d(-24),
        period_end=_d(6),
    )
    inv_auto_prev = Invoice(
        invoice_number="INV-AUTO-10024-06",
        policy_id=auto.id,
        amount_due=142.50,
        amount_paid=142.50,
        due_date=_d(-24),
        status=PaymentStatus.PAID,
        period_start=_d(-54),
        period_end=_d(-24),
    )
    db.add_all([inv_auto, inv_auto_prev])
    await db.flush()
    db.add(
        Payment(
            invoice_id=inv_auto_prev.id,
            amount=142.50,
            status=PaymentStatus.PAID,
            method="mock",
            provider_reference="mock_seed_0001",
        )
    )
    counts["customers"] += 1
    counts["policies"] += 2
    counts["claims"] += 1
    counts["invoices"] += 2

    # ---- Customer 2: James Chen — lapsed auto, past-due -----------------------
    james = Customer(
        first_name="James",
        last_name="Chen",
        email="james.chen@example-synth.test",
        phone="312-555-0177",
        date_of_birth=date(1979, 7, 4),
        zip_code="60614",
        address="88 Lincoln Park Ave, Chicago, IL 60614",
        comm_preference="sms",
        notes="SYNTHETIC demo customer.",
    )
    db.add(james)
    await db.flush()
    auto2 = Policy(
        policy_number="AUTO-10025",
        customer_id=james.id,
        product_type=ProductType.AUTO,
        status=PolicyStatus.LAPSED,
        effective_date=_d(-400),
        renewal_date=_d(-35),
        premium_amount=165.00,
        billing_cadence="monthly",
        autopay=False,
        details={"drivers": ["James Chen"], "state": "IL"},
    )
    db.add(auto2)
    await db.flush()
    db.add(
        Coverage(
            policy_id=auto2.id,
            coverage_type="liability",
            limit_amount=50000,
            description="Liability coverage.",
        )
    )
    inv_pastdue = Invoice(
        invoice_number="INV-AUTO-10025-11",
        policy_id=auto2.id,
        amount_due=165.00,
        amount_paid=0,
        due_date=_d(-40),
        status=PaymentStatus.PAST_DUE,
        period_start=_d(-70),
        period_end=_d(-40),
    )
    db.add(inv_pastdue)
    counts["customers"] += 1
    counts["policies"] += 1
    counts["invoices"] += 1

    # ---- Customer 3: Priya Patel — home claim, life beneficiary, autopay ------
    priya = Customer(
        first_name="Priya",
        last_name="Patel",
        email="priya.patel@example-synth.test",
        phone="404-555-0111",
        date_of_birth=date(1990, 11, 23),
        zip_code="30306",
        address="55 Ponce Ct, Atlanta, GA 30306",
        comm_preference="email",
        notes="SYNTHETIC demo customer.",
    )
    db.add(priya)
    await db.flush()
    home3 = Policy(
        policy_number="HOME-20012",
        customer_id=priya.id,
        product_type=ProductType.HOMEOWNERS,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-120),
        renewal_date=_d(245),
        premium_amount=110.00,
        billing_cadence="monthly",
        autopay=True,
        details={"dwelling": "55 Ponce Ct", "year_built": 2015},
    )
    life3 = Policy(
        policy_number="LIFE-30001",
        customer_id=priya.id,
        product_type=ProductType.LIFE,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-500),
        renewal_date=_d(230),
        premium_amount=45.00,
        billing_cadence="monthly",
        autopay=True,
        details={
            "face_amount": 250000,
            "term_years": 20,
            "beneficiaries": [{"name": "Arjun Patel", "relationship": "spouse", "pct": 100}],
        },
    )
    db.add_all([home3, life3])
    await db.flush()
    db.add_all(
        [
            Coverage(
                policy_id=home3.id,
                coverage_type="dwelling",
                limit_amount=410000,
                deductible=1500,
                exclusions=["flood"],
                description="Dwelling coverage.",
            ),
            Coverage(
                policy_id=life3.id,
                coverage_type="term_life",
                limit_amount=250000,
                description="20-year term life benefit.",
            ),
        ]
    )
    claim3 = Claim(
        claim_number="CLAIM-90002",
        policy_id=home3.id,
        customer_id=priya.id,
        status=ClaimStatus.CLOSED,
        loss_type="water_damage",
        description="Burst pipe under kitchen sink; cabinet and floor damage.",
        date_of_loss=_d(-90),
        reported_date=_d(-88),
        adjuster_name="Tom Reyes",
        adjuster_phone="404-555-0150",
        reserve_amount=8300,
        next_steps=["Claim paid and closed"],
        requested_documents=[],
    )
    db.add(claim3)
    inv_home3 = Invoice(
        invoice_number="INV-HOME-20012-04",
        policy_id=home3.id,
        amount_due=110.00,
        amount_paid=110.00,
        due_date=_d(-2),
        status=PaymentStatus.PAID,
        period_start=_d(-32),
        period_end=_d(-2),
    )
    db.add(inv_home3)
    counts["customers"] += 1
    counts["policies"] += 2
    counts["claims"] += 1
    counts["invoices"] += 1

    # ---- Customer 4: Robert Smith — health + renters --------------------------
    robert = Customer(
        first_name="Robert",
        last_name="Smith",
        email="robert.smith@example-synth.test",
        phone="415-555-0133",
        date_of_birth=date(1965, 2, 28),
        zip_code="94110",
        address="21 Valencia St, San Francisco, CA 94110",
        comm_preference="phone",
        notes="SYNTHETIC demo customer.",
    )
    db.add(robert)
    await db.flush()
    health = Policy(
        policy_number="HEALTH-40001",
        customer_id=robert.id,
        product_type=ProductType.HEALTH,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-60),
        renewal_date=_d(305),
        premium_amount=320.00,
        billing_cadence="monthly",
        autopay=True,
        details={"plan": "PPO Silver", "network": "BayCare"},
    )
    renters = Policy(
        policy_number="RENT-50001",
        customer_id=robert.id,
        product_type=ProductType.RENTERS,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-15),
        renewal_date=_d(350),
        premium_amount=18.00,
        billing_cadence="monthly",
        autopay=False,
        details={"unit": "21 Valencia St #3"},
    )
    db.add_all([health, renters])
    await db.flush()
    db.add_all(
        [
            Coverage(
                policy_id=health.id,
                coverage_type="medical",
                limit_amount=1000000,
                deductible=2500,
                description="Annual in-network medical, deductible then 20% coinsurance.",
            ),
            Coverage(
                policy_id=renters.id,
                coverage_type="personal_property",
                limit_amount=30000,
                deductible=250,
                description="Renters personal property coverage.",
            ),
            Coverage(
                policy_id=renters.id,
                coverage_type="liability",
                limit_amount=100000,
                description="Renters liability coverage.",
            ),
        ]
    )
    counts["customers"] += 1
    counts["policies"] += 2

    # ---- Customer 5: Dana Lee / Acme Landscaping — commercial, umbrella, dispute
    dana = Customer(
        first_name="Dana",
        last_name="Lee",
        email="dana.lee@example-synth.test",
        phone="512-555-0166",
        date_of_birth=date(1972, 9, 9),
        zip_code="78701",
        address="900 Congress Ave, Austin, TX 78701 (Acme Landscaping LLC)",
        comm_preference="email",
        notes="SYNTHETIC commercial contact.",
    )
    db.add(dana)
    await db.flush()
    comm = Policy(
        policy_number="COMM-60001",
        customer_id=dana.id,
        product_type=ProductType.COMMERCIAL,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-250),
        renewal_date=_d(115),
        premium_amount=540.00,
        billing_cadence="monthly",
        autopay=False,
        details={"business": "Acme Landscaping LLC", "employees": 12, "class": "landscaping"},
    )
    umbrella = Policy(
        policy_number="UMB-70001",
        customer_id=dana.id,
        product_type=ProductType.UMBRELLA,
        status=PolicyStatus.ACTIVE,
        effective_date=_d(-250),
        renewal_date=_d(115),
        premium_amount=75.00,
        billing_cadence="monthly",
        autopay=False,
        details={"underlying": ["COMM-60001"]},
    )
    db.add_all([comm, umbrella])
    await db.flush()
    db.add_all(
        [
            Coverage(
                policy_id=comm.id,
                coverage_type="general_liability",
                limit_amount=1000000,
                deductible=1000,
                exclusions=["professional services", "pollution"],
                description="Commercial general liability.",
            ),
            Coverage(
                policy_id=comm.id,
                coverage_type="commercial_property",
                limit_amount=250000,
                deductible=2500,
                description="Business property coverage.",
            ),
            Coverage(
                policy_id=umbrella.id,
                coverage_type="excess_liability",
                limit_amount=2000000,
                description="Umbrella excess over underlying policies.",
            ),
        ]
    )
    claim5 = Claim(
        claim_number="CLAIM-90003",
        policy_id=comm.id,
        customer_id=dana.id,
        status=ClaimStatus.DISPUTED,
        loss_type="property_damage",
        description=(
            "Client alleges lawn equipment damaged an irrigation system; coverage disputed."
        ),
        date_of_loss=_d(-45),
        reported_date=_d(-43),
        adjuster_name="Priya Nair",
        adjuster_phone="512-555-0188",
        reserve_amount=15000,
        next_steps=["Under coverage review", "Legal referral pending"],
        requested_documents=["Incident report", "Repair invoices", "Site photos"],
    )
    db.add(claim5)
    inv_comm = Invoice(
        invoice_number="INV-COMM-60001-09",
        policy_id=comm.id,
        amount_due=540.00,
        amount_paid=0,
        due_date=_d(9),
        status=PaymentStatus.PENDING,
        period_start=_d(-21),
        period_end=_d(9),
    )
    db.add(inv_comm)
    counts["customers"] += 1
    counts["policies"] += 2
    counts["claims"] += 1
    counts["invoices"] += 1

    await db.commit()
    return counts


# Demo customer directory surfaced by the API for the synthetic customer selector.
DEMO_CUSTOMERS = [
    {
        "name": "Maria Alvarez",
        "policy_number": "AUTO-10024",
        "zip_code": "78258",
        "date_of_birth": "1985-03-12",
        "scenario": "Active auto + home, open collision claim, payment due",
    },
    {
        "name": "James Chen",
        "policy_number": "AUTO-10025",
        "zip_code": "60614",
        "date_of_birth": "1979-07-04",
        "scenario": "Lapsed auto policy, past-due balance",
    },
    {
        "name": "Priya Patel",
        "policy_number": "HOME-20012",
        "zip_code": "30306",
        "date_of_birth": "1990-11-23",
        "scenario": "Home (closed water claim) + life w/ beneficiary, autopay",
    },
    {
        "name": "Robert Smith",
        "policy_number": "HEALTH-40001",
        "zip_code": "94110",
        "date_of_birth": "1965-02-28",
        "scenario": "Health PPO + renters",
    },
    {
        "name": "Dana Lee (Acme Landscaping)",
        "policy_number": "COMM-60001",
        "zip_code": "78701",
        "date_of_birth": "1972-09-09",
        "scenario": "Commercial + umbrella, disputed claim (escalation)",
    },
]
