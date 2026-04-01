"""Step handlers for the Vault access provisioning workflow.

Demonstrates a realistic multi-step provisioning flow with sync steps
for infrastructure operations and an async step for approval polling.
"""

from __future__ import annotations

from pydantic import BaseModel

from workchain import PollPolicy, async_step, step

# ============================================================================
# Simulated external state
# ============================================================================

_approval_polls: dict[str, int] = {}
_ad_group_polls: dict[str, int] = {}


# ============================================================================
# Config / context models
# ============================================================================


class ServiceContext(BaseModel):
    """Shape of the shared workflow context for this provisioning flow."""
    service_name: str = "unknown"
    requested_by: str = "unknown"
    # Populated by upstream steps:
    service_record_id: str | None = None
    policy_name: str | None = None
    group_name: str | None = None
    group_type: str | None = None


class ApprovalResult(BaseModel):
    request_id: str


class ServiceDetailsConfig(BaseModel):
    mongo_uri: str = "mongodb://localhost:27017"
    collection: str = "services"


class VaultPolicyConfig(BaseModel):
    secrets_path: str


class ADGroupConfig(BaseModel):
    group_name: str
    group_type: str = "readers"


# ============================================================================
# Step 1: Request approval (async — polls approval system)
# ============================================================================


async def check_approval(config: dict, context: dict, result: dict) -> dict:
    """Poll the approval system. Simulates approval after 2 polls."""
    res = ApprovalResult(**result)
    _approval_polls[res.request_id] = _approval_polls.get(res.request_id, 0) + 1
    count = _approval_polls[res.request_id]

    if count >= 2:
        print(f"  [approval] APPROVED (poll {count})")
        return {"complete": True, "progress": 1.0, "message": "Approved"}

    print(f"  [approval] Pending (poll {count}/2)")
    return {"complete": False, "progress": count / 2.0, "message": "Awaiting review"}


@async_step(
    name="request_approval",
    completeness_check=check_approval,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=60.0),
)
async def request_approval(config: dict, context: dict) -> dict:
    """Publish an approval request and wait for it to be granted."""
    ctx = ServiceContext(**context)
    request_id = f"approval-{ctx.service_name}-{ctx.requested_by}"

    print(f"  [approval] Submitted request for '{ctx.service_name}' by {ctx.requested_by}")
    return {"request_id": request_id}


# ============================================================================
# Step 2: Write service details (sync)
# ============================================================================


@step(name="write_service_details")
async def write_service_details(config: dict, context: dict) -> dict:
    """Write service record to MongoDB."""
    cfg = ServiceDetailsConfig(**config)
    ctx = ServiceContext(**context)

    # In production: insert_one({...})
    record_id = "mock-record-id"
    print(f"  [service] Inserted '{ctx.service_name}' into {cfg.collection}")
    return {"service_record_id": record_id}


# ============================================================================
# Step 3: Create Vault policy (sync)
# ============================================================================


@step(name="create_vault_policy")
async def create_vault_policy(config: dict, context: dict) -> dict:
    """Create a HashiCorp Vault policy for the service."""
    cfg = VaultPolicyConfig(**config)
    ctx = ServiceContext(**context)
    policy_name = f"{ctx.service_name}-secrets"

    print(f"  [vault] Created policy '{policy_name}' for path '{cfg.secrets_path}'")
    return {"policy_name": policy_name}


# ============================================================================
# Step 4: Apply Vault policy (sync)
# ============================================================================


@step(name="apply_vault_policy")
async def apply_vault_policy(config: dict, context: dict) -> dict:
    """Bind the Vault policy to an AppRole auth method."""
    ctx = ServiceContext(**context)
    policy_name = ctx.policy_name or "unknown"

    print(f"  [vault] Attached policy '{policy_name}' to role '{ctx.service_name}'")
    return {"role": ctx.service_name, "policy": policy_name}


# ============================================================================
# Step 5: Create AD group (sync — initiates async operation)
# ============================================================================


@step(name="create_ad_group")
async def create_ad_group(config: dict, context: dict) -> dict:
    """Initiate creation of an Active Directory group."""
    cfg = ADGroupConfig(**config)

    print(f"  [ad] Initiated AD group creation: {cfg.group_name} ({cfg.group_type})")
    return {"group_name": cfg.group_name, "group_type": cfg.group_type}


# ============================================================================
# Step 6: Await AD group (async — polls AD until group exists)
# ============================================================================


async def check_ad_group(config: dict, context: dict, result: dict) -> dict:
    """Poll AD for group existence. Simulates readiness after 3 polls."""
    cfg = ADGroupConfig(**config)
    group_name = result.get("group_name", cfg.group_name)
    _ad_group_polls[group_name] = _ad_group_polls.get(group_name, 0) + 1
    count = _ad_group_polls[group_name]

    if count >= 3:
        print(f"  [ad] Group '{group_name}' confirmed (poll {count})")
        return {"complete": True, "progress": 1.0, "message": "Group ready"}

    print(f"  [ad] Group '{group_name}' not ready (poll {count}/3)")
    return {"complete": False, "progress": count / 3.0}


@async_step(
    name="await_ad_group",
    completeness_check=check_ad_group,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.5, max_interval=10.0, timeout=120.0),
)
async def await_ad_group(config: dict, context: dict) -> dict:
    """Wait for an AD group to become available."""
    cfg = ADGroupConfig(**config)
    print(f"  [ad] Polling for group '{cfg.group_name}'...")
    return {"group_name": cfg.group_name}
