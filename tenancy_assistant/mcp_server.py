import sys
import json
import traceback
import oci
from datetime import datetime, timezone
from oci.usage_api import models as usage_api_models


# Cache for compartment names so we don't call Identity over and over
_COMPARTMENT_NAME_CACHE = {}

def resolve_compartment_name(identity_client, compartment_ocid: str) -> str:
    """
    Resolve a compartment OCID to a friendly name, with simple in-memory cache.
    If resolution fails, we just return the OCID itself.
    """
    if not compartment_ocid:
        return "UNKNOWN"

    if compartment_ocid in _COMPARTMENT_NAME_CACHE:
        return _COMPARTMENT_NAME_CACHE[compartment_ocid]

    try:
        resp = identity_client.get_compartment(compartment_ocid)
        name = resp.data.name
    except Exception:
        name = compartment_ocid  # fallback to OCID string

    _COMPARTMENT_NAME_CACHE[compartment_ocid] = name
    return name



# ---------- OCI CLIENT SETUP ----------

def get_oci_client_config(profile_name: str = "DEFAULT"):
    return oci.config.from_file("~/.oci/config", profile_name)


def get_identity_client(config):
    return oci.identity.IdentityClient(config)


def get_core_client(config):
    return oci.core.VirtualNetworkClient(config)


def list_all_compartments(identity_client, tenancy_id):
    compartments = []
    tenancy = identity_client.get_tenancy(tenancy_id).data
    compartments.append(tenancy)

    response = oci.pagination.list_call_get_all_results(
        identity_client.list_compartments,
        tenancy_id,
        compartment_id_in_subtree=True,
        access_level="ANY",
        lifecycle_state=oci.identity.models.Compartment.LIFECYCLE_STATE_ACTIVE,
    )
    compartments.extend(response.data)
    return compartments


def tool_get_public_ip_summary(params, config):
    compartment_ocid = params.get("compartment_ocid")
    scope = (params.get("scope") or "ALL").upper()

    identity_client = get_identity_client(config)
    core_client = get_core_client(config)
    tenancy_id = config["tenancy"]

    if compartment_ocid:
        comp = identity_client.get_compartment(compartment_ocid).data
        compartments = [comp]
    else:
        compartments = list_all_compartments(identity_client, tenancy_id)

    total_count = 0
    by_scope = {"EPHEMERAL": 0, "RESERVED": 0}
    items = []

    for comp in compartments:
        comp_id = comp.id

        response = oci.pagination.list_call_get_all_results(
            core_client.list_public_ips,
            scope="REGION",
            compartment_id=comp_id,
        )

        for ip in response.data:
            lifetime = ip.lifetime
            if lifetime == "EPHEMERAL":
                by_scope["EPHEMERAL"] += 1
            elif lifetime == "RESERVED":
                by_scope["RESERVED"] += 1

            total_count += 1
            items.append({
                "id": ip.id,
                "ip_address": ip.ip_address,
                "compartment_id": ip.compartment_id,
                "lifetime": ip.lifetime,
                "lifecycle_state": ip.lifecycle_state,
                "assigned_entity_id": ip.assigned_entity_id,
            })

    if scope in ("EPHEMERAL", "RESERVED"):
        filtered_items = [i for i in items if i["lifetime"] == scope]
        total_filtered = len(filtered_items)
    else:
        filtered_items = items
        total_filtered = total_count

    return {
        "total_count": total_filtered,
        "by_scope": by_scope,
        "items": filtered_items,
    }

# ---------- COST SUMMARY TOOL (USAGE API) ----------------------------------------------------------------------------------

def get_usage_client(config):
    """
    Create an OCI Usage API client using the correct SDK module path.
    Your environment exposes it as oci.usage_api.UsageapiClient.
    """
    from oci.usage_api import UsageapiClient
    return UsageapiClient(config)


def iso_now_utc():
    return datetime.now(timezone.utc)

def normalize_to_utc_midnight(dt: datetime) -> datetime:
    """
    Convert a datetime to UTC and snap it to 00:00:00.000000.
    This matches Usage API's requirement that hours, minutes, seconds,
    and fractions must be 0.
    """
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)

def default_month_range():
    """
    Default time window: from first day of current month at 00:00 UTC
    until TODAY at 00:00 UTC.
    """
    now = iso_now_utc()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = normalize_to_utc_midnight(now)
    return start, end



def tool_get_cost_summary(params, config):
    """
    Get summarized cost from OCI Usage API.

    Params (JSON):
      - time_start (string, optional ISO 8601)
      - time_end (string, optional ISO 8601)
      - granularity (string, optional) : 'DAILY' or 'MONTHLY' (default 'MONTHLY')
      - group_by (string, optional)    : 'COMPARTMENT' | 'SERVICE' | 'RESOURCE' (default 'COMPARTMENT')
      - compartment_ocid (string, optional) : limit to a single compartment

    Returns JSON like:
      {
        "total_cost": 123.45,
        "currency": "USD",
        "granularity": "MONTHLY",
        "time_start": "...",
        "time_end": "...",
        "group_by": "COMPARTMENT",
        "items": [
          { "key": "...", "label": "...", "cost": 12.34 }
        ]
      }
    """
    usage_client = get_usage_client(config)
    tenancy_id = config["tenancy"]

    # --- Parse inputs ---
    time_start_str = params.get("time_start")
    time_end_str = params.get("time_end")
    granularity = (params.get("granularity") or "MONTHLY").upper()
    group_by = (params.get("group_by") or "COMPARTMENT").upper()
    compartment_ocid = params.get("compartment_ocid")

    # Time window
    if time_start_str and time_end_str:
        time_start = normalize_to_utc_midnight(datetime.fromisoformat(time_start_str))
        time_end = normalize_to_utc_midnight(datetime.fromisoformat(time_end_str))
    else:
        time_start, time_end = default_month_range()


    # Map group_by to OCI dimension names
    if group_by == "COMPARTMENT":
        group_by_dim = ["compartmentId"]
    elif group_by == "SERVICE":
        group_by_dim = ["service"]
    elif group_by == "RESOURCE":
        group_by_dim = ["resourceId"]
    else:
        group_by_dim = ["compartmentId"]
        group_by = "COMPARTMENT"  # normalize

    # --- Compartment depth if grouping by compartment ---
    compartment_depth = None
    if group_by == "COMPARTMENT":
        compartment_depth = 6  # adjust if you want fewer levels

    # --- Build Usage API request details ---
    details_kwargs = dict(
        tenant_id=tenancy_id,
        granularity=granularity,           # 'DAILY' or 'MONTHLY'
        time_usage_started=time_start,
        time_usage_ended=time_end,
        group_by=group_by_dim,
    )
    if compartment_depth is not None:
        details_kwargs["compartment_depth"] = compartment_depth

    details = usage_api_models.RequestSummarizedUsagesDetails(**details_kwargs)


    # Optional filter for a specific compartment
    if compartment_ocid:
        details.filter = oci.usage_api.models.Filter(
            dimensions=[
                oci.usage_api.models.Dimension(
                    key="compartmentId",
                    value=compartment_ocid,
                )
            ]
        )

    # --- Call Usage API ---
    response = usage_client.request_summarized_usages(details)
    usages = response.data.items  # list of SummarizedUsages


    total_cost = 0.0
    buckets_by_key = {}
    buckets_by_compartment_ocid = {}
    currency = "USD"  # default; will be overwritten if present

    # Identity client only needed if we group by compartment
    identity_client = None
    if group_by == "COMPARTMENT":
        identity_client = get_identity_client(config)

    for u in usages:
        # Amount and currency
        amount = getattr(u, "computed_amount", None)
        if amount is None:
            amount = getattr(u, "cost", 0.0)
        currency = getattr(u, "currency", currency) or currency
        total_cost += amount

        if group_by == "COMPARTMENT":
            # Many OCI SDKs expose these as attributes on the UsageSummary model
            compartment_ocid = (
                getattr(u, "compartment_id", None)
                or getattr(u, "compartmentId", None)
                or getattr(u, "compartment_ocid", None)
            )
            if not compartment_ocid:
                compartment_ocid = "UNKNOWN"

            buckets_by_compartment_ocid.setdefault(compartment_ocid, 0.0)
            buckets_by_compartment_ocid[compartment_ocid] += amount

        elif group_by == "SERVICE":
            service_key = (
                getattr(u, "service", None)
                or getattr(u, "service_name", None)
                or getattr(u, "serviceName", None)
                or "UNKNOWN"
            )
            buckets_by_key.setdefault(service_key, 0.0)
            buckets_by_key[service_key] += amount

        elif group_by == "RESOURCE":
            resource_key = (
                getattr(u, "resource_id", None)
                or getattr(u, "resourceId", None)
                or getattr(u, "resource_ocid", None)
                or "UNKNOWN"
            )
            buckets_by_key.setdefault(resource_key, 0.0)
            buckets_by_key[resource_key] += amount

        else:
            buckets_by_key.setdefault("UNKNOWN", 0.0)
            buckets_by_key["UNKNOWN"] += amount

    # Build items list with labels
    items = []

    if group_by == "COMPARTMENT":
        for ocid_key, value in buckets_by_compartment_ocid.items():
            # Try to get a friendly name directly from the UsageSummary if possible
            # (this is per-usage, but we only need any one name per compartment;
            # we will still fall back to IdentityClient if needed.)
            label = None

            # Try to get compartment_name from one of the usage records.
            # Since we don't have direct access here, we rely on Identity for now.
            if ocid_key != "UNKNOWN":
                label = resolve_compartment_name(identity_client, ocid_key)
            else:
                label = "UNKNOWN"

            items.append(
                {
                    "key": ocid_key,
                    "label": label,
                    "cost": round(value, 2),
                }
            )
    else:
        for k, v in buckets_by_key.items():
            items.append(
                {
                    "key": k,
                    "label": k,
                    "cost": round(v, 2),
                }
            )

    result = {
        "total_cost": round(total_cost, 2),
        "currency": currency,
        "granularity": granularity,
        "time_start": time_start.isoformat(),
        "time_end": time_end.isoformat(),
        "group_by": group_by,
        "items": items,
    }

    return result





def handle_request(req: dict, config):
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")

    try:
        if method == "getPublicIpSummary":
            result = tool_get_public_ip_summary(params, config)
        elif method == "getCostSummary":
            result = tool_get_cost_summary(params, config)
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown method: {method}",
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32000,
                "message": str(e),
                "data": traceback.format_exc(),
            },
        }


def main():
    config = get_oci_client_config(profile_name="DEFAULT")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        resp = handle_request(req, config)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
