"""Step definitions for the Vault access provisioning workflow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from workchain import Context, EventStep, PollingStep, Step, StepResult

# ============================================================================
# Configuration Models
# ============================================================================


class ApprovalRequestConfig(BaseModel):
    """Configuration for the Solace approval request step."""

    solace_queue: str
    response_queue: str


class ServiceDetailsConfig(BaseModel):
    """Configuration for writing service details to MongoDB."""

    mongo_uri: str
    database: str
    collection: str


class VaultPolicyConfig(BaseModel):
    """Configuration for Vault policy operations."""

    vault_addr: str
    secrets_path: str  # e.g. "secret/data/myapp"


class ADGroupConfig(BaseModel):
    """Configuration for Active Directory group operations."""

    group_name: str
    group_type: str  # "readers" or "writers"
    ad_server: str


# ============================================================================
# Step 1: RequestApprovalStep — EventStep (Solace publish → suspend → resume)
# ============================================================================


class RequestApprovalStep(EventStep[ApprovalRequestConfig]):
    """
    Publish an approval request to a Solace queue, then suspend.

    The workflow pauses here until an external consumer reads the approval
    response from the response queue and calls runner.resume() with the
    correlation_id and approval payload.

    On denial, raises ValueError which the runner converts to a FAILED step,
    cascading failure to all downstream steps.
    """

    Config = ApprovalRequestConfig

    def execute(self, context: Context) -> StepResult:
        service_name = context.get("service_name")
        requested_by = context.get("requested_by")

        correlation_id = f"approval-{service_name}-{requested_by}"
        message = {
            "service_name": service_name,
            "requested_by": requested_by,
            "correlation_id": correlation_id,
        }

        # In production: solace_client.publish(self.config.solace_queue, message)
        print(f"  [request_approval] Published to {self.config.solace_queue}: {message}")
        print(f"  [request_approval] Suspended, awaiting response on {self.config.response_queue}")

        return StepResult.suspend(correlation_id=correlation_id)

    def on_resume(self, payload: dict[str, Any], context: Context) -> dict[str, Any]:
        approved = payload.get("approved", False)

        if not approved:
            reason = payload.get("reason", "no reason given")
            print(f"  [request_approval] DENIED: {reason}")
            raise ValueError(f"Approval denied: {reason}")

        context.set("approval", {
            "approved": True,
            "approver": payload.get("approver"),
            "approved_at": payload.get("approved_at"),
        })

        print(f"  [request_approval] APPROVED by {payload.get('approver')}")
        return {"approved": True, "approver": payload.get("approver")}


# ============================================================================
# Step 2: WriteServiceDetailsStep — Standard Step (MongoDB write)
# ============================================================================


class WriteServiceDetailsStep(Step[ServiceDetailsConfig]):
    """
    Write the service/user details and approval record to MongoDB.

    Reads service_name and approval from context (set by upstream steps),
    then inserts a document into the configured collection.
    """

    Config = ServiceDetailsConfig

    def execute(self, context: Context) -> StepResult:
        # In production, build a doc from context and insert:
        # client = MongoClient(self.config.mongo_uri)
        # doc = {"service_name": context.get("service_name"), ...}
        # result = client[self.config.database][self.config.collection].insert_one(doc)
        # record_id = str(result.inserted_id)
        record_id = "mock-record-id"

        context.set("service_record_id", record_id)
        print(f"  [write_service_details] Inserted into {self.config.database}.{self.config.collection}")
        return StepResult.complete(output={"service_record_id": record_id})


# ============================================================================
# Step 3: CreateVaultPolicyStep — Standard Step (Vault API)
# ============================================================================


class CreateVaultPolicyStep(Step[VaultPolicyConfig]):
    """
    Create a HashiCorp Vault policy granting read/list on the secrets path.

    Stores the policy name in context for the subsequent apply step.
    """

    Config = VaultPolicyConfig

    def execute(self, context: Context) -> StepResult:
        service_name = context.get("service_name")
        policy_name = f"{service_name}-secrets"

        policy_hcl = (
            f'path "{self.config.secrets_path}/*" {{\n'
            f'    capabilities = ["read", "list"]\n'
            f"}}"
        )

        # In production:
        # vault_client.sys.create_or_update_policy(policy_name, policy_hcl)
        print(f"  [create_vault_policy] Created policy '{policy_name}'")
        print(f"       {policy_hcl}")

        context.set("vault_policy_name", policy_name)
        return StepResult.complete(output={"policy_name": policy_name})


# ============================================================================
# Step 4: ApplyVaultPolicyStep — Standard Step (Vault API)
# ============================================================================


class ApplyVaultPolicyStep(Step[VaultPolicyConfig]):
    """
    Bind the Vault policy to an auth method (e.g. AppRole) so the service
    can authenticate and access secrets.
    """

    Config = VaultPolicyConfig

    def execute(self, context: Context) -> StepResult:
        policy_name = context.get("vault_policy_name")
        service_name = context.get("service_name")

        # In production:
        # vault_client.auth.approle.create_or_update_approle(
        #     role_name=service_name, policies=[policy_name]
        # )
        print(f"  [apply_vault_policy] Attached policy '{policy_name}' to role '{service_name}'")
        return StepResult.complete(output={"role": service_name, "policy": policy_name})


# ============================================================================
# Step 5: CreateADGroupStep — Standard Step (AD API, initiates async op)
# ============================================================================


class CreateADGroupStep(Step[ADGroupConfig]):
    """
    Initiate creation of an Active Directory group.

    AD group creation is asynchronous — this step fires the request and
    completes immediately. The paired AwaitADGroupStep polls until the
    group is actually available.
    """

    Config = ADGroupConfig

    def execute(self, context: Context) -> StepResult:
        group_name = self.config.group_name
        group_type = self.config.group_type

        # In production: ad_client.create_group(group_name, group_type=group_type)
        print(f"  [create_{group_type}_group] Initiated AD group creation: {group_name}")

        context.set(f"ad_group_{group_type}", group_name)
        return StepResult.complete(output={"group_name": group_name, "group_type": group_type})


# ============================================================================
# Step 6: AwaitADGroupStep — PollingStep (polls AD until group exists)
# ============================================================================


class AwaitADGroupStep(PollingStep[ADGroupConfig]):
    """
    Poll Active Directory until the group is confirmed to exist.

    Polls every 10 seconds with a 5-minute timeout. In the demo, completes
    after 3 checks to simulate AD replication delay.
    """

    Config = ADGroupConfig
    poll_interval_seconds = 10
    timeout_seconds = 300

    def __init__(self, config: ADGroupConfig | None = None) -> None:
        super().__init__(config=config)
        self._check_count = 0

    def check(self, context: Context) -> bool:
        self._check_count += 1
        group_name = self.config.group_name

        # In production: return ad_client.group_exists(group_name)
        # For demo: simulate group appearing after 3 checks
        ready = self._check_count >= 3
        if ready:
            print(f"  [await_{self.config.group_type}_group] Group '{group_name}' confirmed (check {self._check_count})")
        else:
            print(f"  [await_{self.config.group_type}_group] Group '{group_name}' not yet ready (check {self._check_count})")
        return ready

    def on_complete(self, context: Context) -> dict[str, Any]:
        return {
            "group_name": self.config.group_name,
            "group_type": self.config.group_type,
            "confirmed": True,
            "checks_required": self._check_count,
        }
