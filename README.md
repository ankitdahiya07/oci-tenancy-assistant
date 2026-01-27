# OCI Tenancy Assistant

A small OCI tenancy assistant that combines OCI Generative AI with a local MCP-style JSON-RPC server to answer tenancy questions and visualize public IP and cost summaries.

## Repo structure

- `tenancy_assistant/` - core Python package (GenAI client + MCP server)
- `apps/` - Streamlit UI

## Prerequisites

- Python 3.9+
- OCI Python SDK (`oci`)
- Streamlit (`streamlit`)
- OCI config file at `~/.oci/config`

## Setup

Set the required environment variables (do not hardcode OCIDs in source):

- `GENAI_COMPARTMENT_ID` - compartment OCID for the GenAI service
- `GENAI_ENDPOINT` - Generative AI inference endpoint (region-specific)
- `GENAI_MODEL_ID` - model OCID
- `GENAI_CONFIG_PROFILE` (optional, default: `DEFAULT`)

Example (PowerShell):

```powershell
$env:GENAI_COMPARTMENT_ID = "ocid1.compartment.oc1..example"
$env:GENAI_ENDPOINT = "https://inference.generativeai.<region>.oci.oraclecloud.com"
$env:GENAI_MODEL_ID = "ocid1.generativeaimodel.oc1.<region>.example"
$env:GENAI_CONFIG_PROFILE = "DEFAULT"
```

Install dependencies:

```powershell
pip install oci streamlit
```

## Usage

CLI (GenAI + MCP tool flow):

```powershell
python -m tenancy_assistant.genai_assistant "How many public IPs do I have?"
```

Streamlit app:

```powershell
streamlit run apps\streamlit_app.py
```

## Notes

- `tenancy_assistant/mcp_server.py` exposes `getPublicIpSummary` and `getCostSummary` over JSON-RPC.
- The Streamlit UI caches tenancy snapshots for 10 minutes to keep responses fast.
- If you plan to publish this repo, keep real OCIDs and tenancy identifiers out of source control.
