"""The vendor approval queue: drafted payees, and the endpoint that makes them payable."""

import uuid

import pytest
import pytest_asyncio

from app.agent.tools import lookup_vendor
from app.models import Vendor


@pytest_asyncio.fixture
async def drafted_vendor(db_session):
    """A payee the agent drafted, waiting on a human.

    Written directly rather than via `draft_vendor`, since what's under test
    here is the approval endpoint, not vendor drafting.
    """
    vendor = Vendor(
        name="Nonesuch Trading LLC",
        normalized_name="nonesuch trading llc",
        bank_details="IBAN GB00NONE00000000000009",
        approval_status="pending_approval",
        created_by="agent",
    )
    db_session.add(vendor)
    await db_session.commit()
    await db_session.refresh(vendor)
    return vendor


@pytest.mark.asyncio
async def test_lists_only_pending_vendors(client, drafted_vendor, seeded_vendor):
    response = await client.get("/vendors/pending")

    assert response.status_code == 200
    names = [vendor["name"] for vendor in response.json()]
    assert names == ["Nonesuch Trading LLC"]


@pytest.mark.asyncio
async def test_pending_vendor_carries_its_unverified_bank_details(client, drafted_vendor):
    """The approval view labels these unverified rather than hiding them --
    they're the only thing a human has to check the payee against."""
    body = (await client.get("/vendors/pending")).json()

    assert body[0]["bank_details"] == drafted_vendor.bank_details


@pytest.mark.asyncio
async def test_approving_activates_the_vendor(client, drafted_vendor, db_session):
    response = await client.post(f"/vendors/{drafted_vendor.id}/approve")

    assert response.status_code == 200
    assert response.json()["approval_status"] == "active"

    await db_session.refresh(drafted_vendor)
    assert drafted_vendor.approval_status == "active"


@pytest.mark.asyncio
async def test_an_approved_vendor_then_resolves(client, drafted_vendor, db_session):
    """The whole point of the queue: approval is what makes a payee payable, and
    nothing else does."""
    before = await lookup_vendor(db_session, vendor_name=drafted_vendor.name)
    await client.post(f"/vendors/{drafted_vendor.id}/approve")
    after = await lookup_vendor(db_session, vendor_name=drafted_vendor.name)

    assert before["match"] == "drafted"
    assert after["match"] == "resolved"


@pytest.mark.asyncio
async def test_approving_removes_the_vendor_from_the_pending_list(client, drafted_vendor):
    await client.post(f"/vendors/{drafted_vendor.id}/approve")

    response = await client.get("/vendors/pending")
    assert response.json() == []


@pytest.mark.asyncio
async def test_approving_an_unknown_vendor_is_a_404(client):
    response = await client.post(f"/vendors/{uuid.uuid4()}/approve")
    assert response.status_code == 404
