import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, Any

import oci


# =========================
# 1. OCI GEN AI CLIENT SETUP
# =========================

# Use your provided values as defaults; you can override via env vars if needed.
GENAI_COMPARTMENT_ID = os.environ.get(
    "GENAI_COMPARTMENT_ID",
    "",
)

GENAI_CONFIG_PROFILE = os.environ.get("GENAI_CONFIG_PROFILE", "DEFAULT")

GENAI_ENDPOINT = os.environ.get(
    "GENAI_ENDPOINT",
    "",
)

GENAI_MODEL_ID = os.environ.get(
    "GENAI_MODEL_ID",
    "",
)

MCP_SERVER_TIMEOUT_SEC = 600


def _require_env(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(
            f"{name} is not set. Configure it as an environment variable before running."
        )
    return value


def get_genai_client() -> oci.generative_ai_inference.GenerativeAiInferenceClient:
    """
    Create an OCI Generative AI Inference client using the same pattern as your sample.
    """
    endpoint = _require_env("GENAI_ENDPOINT", GENAI_ENDPOINT)
    config = oci.config.from_file("~/.oci/config", GENAI_CONFIG_PROFILE)
    client = oci.generative_ai_inference.GenerativeAiInferenceClient(
        config=config,
        service_endpoint=endpoint,
        retry_strategy=oci.retry.NoneRetryStrategy(),
        timeout=(10, 240),
    )
    return client

def genai_chat(client, prompt: str) -> str:
    """
    Send a single prompt to the OCI GenAI chat endpoint and return the text output.
    This version is tailored to the response shape you showed.
    """
    # Build the chat request
    chat_detail = oci.generative_ai_inference.models.ChatDetails()

    # Message content
    content = oci.generative_ai_inference.models.TextContent()
    content.text = prompt

    message = oci.generative_ai_inference.models.Message()
    message.role = "USER"
    message.content = [content]

    chat_request = oci.generative_ai_inference.models.GenericChatRequest()
    chat_request.api_format = (
        oci.generative_ai_inference.models.BaseChatRequest.API_FORMAT_GENERIC
    )
    chat_request.messages = [message]
    chat_request.max_tokens = 2048
    chat_request.temperature = 0.1  # low temp for deterministic JSON
    chat_request.frequency_penalty = 0
    chat_request.presence_penalty = 0
    chat_request.top_p = 1
    chat_request.top_k = 0

    # On-demand serving mode with your model
    model_id = _require_env("GENAI_MODEL_ID", GENAI_MODEL_ID)
    compartment_id = _require_env("GENAI_COMPARTMENT_ID", GENAI_COMPARTMENT_ID)
    chat_detail.serving_mode = oci.generative_ai_inference.models.OnDemandServingMode(
        model_id=model_id
    )
    chat_detail.chat_request = chat_request
    chat_detail.compartment_id = compartment_id

    response = client.chat(chat_detail)

    # --- Extract text based on your actual shape ---
    data = response.data              # ChatResult
    chat_resp = data.chat_response    # ChatResponse
    choices = chat_resp.choices       # list of choices
    if not choices:
        raise RuntimeError("No choices returned from GenAI.")

    first_choice = choices[0]
    msg = first_choice.message
    contents = msg.content
    if not contents:
        raise RuntimeError("No message content returned from GenAI.")

    text_obj = contents[0]
    if not hasattr(text_obj, "text") or not text_obj.text:
        raise RuntimeError("No text field in message content.")

    return text_obj.text.strip()



# =========================
# 2. CALL MCP SERVER (YOUR OCI TOOL)
# =========================

def call_mcp_server(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Spawn the local mcp_server.py, send one JSON-RPC request via stdin,
    read one JSON response from stdout, and return the 'result' dict.
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    server_path = Path(__file__).with_name("mcp_server.py")
    if not server_path.exists():
        raise RuntimeError(f"MCP server not found at {server_path}")

    proc = subprocess.Popen(
        [sys.executable, str(server_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout, stderr = proc.communicate(
        json.dumps(request) + "\n", timeout=MCP_SERVER_TIMEOUT_SEC
    )

    if stderr:
        print("=== MCP server stderr ===", file=sys.stderr)
        print(stderr, file=sys.stderr)
        print("=========================", file=sys.stderr)

    stdout = stdout.strip()
    if not stdout:
        raise RuntimeError("No output from MCP server.")

    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    response = json.loads(last_line)

    if "error" in response:
        raise RuntimeError(f"MCP server returned error: {response['error']}")

    return response["result"]

def get_public_ip_summary(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Convenience wrapper to call getPublicIpSummary via MCP.
    This does NOT involve the LLM. It just returns the raw JSON.
    """
    if params is None:
        params = {"scope": "ALL"}
    return call_mcp_server("getPublicIpSummary", params)

def get_cost_summary(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience wrapper to call getCostSummary via MCP.
    Example params:
      {
        "granularity": "MONTHLY",
        "group_by": "COMPARTMENT"
      }
    """
    return call_mcp_server("getCostSummary", params)

def get_cloud_guard_summary(params: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Convenience wrapper to call getCloudGuardSummary via MCP.
    Example params:
      {
        "include_endpoints": true,
        "max_problems": 10,
        "max_endpoints_per_problem": 10
      }
    """
    if params is None:
        params = {"include_endpoints": True}
    return call_mcp_server("getCloudGuardSummary", params)


# =========================
# 3. TOOL SELECTION + ANSWERING LOGIC
# =========================

def decide_tool_and_args(client, question: str) -> Dict[str, Any]:
    """
    Ask the model (on OCI) which tool to call and with what arguments.
    We use a strict JSON format to keep it model-agnostic.
    """
    system_instructions = """
You are an OCI Tenancy Assistant tool router.
You MUST respond with a single JSON object ONLY, no extra text.

Available tools:
- name: getPublicIpSummary
  description: Get a summary of public IP addresses in the OCI tenancy
               or a specific compartment.
  parameters:
    - compartment_ocid (string, optional)
    - scope (string, optional): one of ALL, EPHEMERAL, RESERVED. Default: ALL.
- name: getCostSummary
  description: Get a cost summary for the tenancy (Usage API).
  parameters:
    - time_start (string, optional ISO 8601)
    - time_end (string, optional ISO 8601)
    - granularity (string, optional): DAILY or MONTHLY
    - group_by (string, optional): COMPARTMENT, SERVICE, RESOURCE
- name: getCloudGuardSummary
  description: Get Cloud Guard targets, problems, and (optionally) problem endpoints.
  parameters:
    - compartment_ocid (string, optional)
    - include_endpoints (bool, optional)
    - max_problems (int, optional)
    - max_endpoints_per_problem (int, optional)

Your job:
- Read the user question.
- Choose the most appropriate tool (or null if none apply).
- For public IP questions, use getPublicIpSummary with scope ALL unless the user asks EPHEMERAL or RESERVED.
- For cost questions, use getCostSummary (default granularity MONTHLY, group_by COMPARTMENT).
- For Cloud Guard questions, use getCloudGuardSummary; include_endpoints should be true if the user asks about endpoints.

Example outputs:
  { "tool": "getPublicIpSummary", "arguments": { "scope": "ALL" } }
  { "tool": "getCostSummary", "arguments": { "granularity": "MONTHLY", "group_by": "COMPARTMENT" } }
  { "tool": "getCloudGuardSummary", "arguments": { "include_endpoints": true, "max_problems": 10, "max_endpoints_per_problem": 10 } }

If the user's question is NOT about any tool above, output:
  {
    "tool": null,
    "arguments": {}
  }

Rules:
- Output MUST be valid JSON.
- No explanation, no markdown, no comments, no extra text.
"""

    prompt = f"{system_instructions}\n\nUser question:\n{question}\n\nJSON output:"

    raw = genai_chat(client, prompt).strip()

    # Try direct JSON parse first
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract the first { ... } block
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = raw[start : end + 1]
            data = json.loads(snippet)
        else:
            raise

    tool = data.get("tool")
    arguments = data.get("arguments") or {}
    return {"tool": tool, "arguments": arguments}


def answer_with_tool_result(
    client, question: str, tool_name: str, tool_result: Dict[str, Any]
) -> str:
    """
    Ask the model to write a nice explanation using the tool result JSON.
    """
    system_instructions = """
You are an OCI Tenancy Assistant.
You will be given:
- A user question.
- The name of a tool that was executed.
- The JSON result from that tool.

Your job:
- Read the JSON carefully.
- Answer the user's question in clear, concise natural language.
- Explicitly mention key numbers like total counts and breakdowns.
- Do NOT show the raw JSON. Summarize it instead.
"""

    prompt = (
        f"{system_instructions}\n\n"
        f"User question:\n{question}\n\n"
        f"Tool used: {tool_name}\n\n"
        f"Tool JSON result:\n{json.dumps(tool_result, indent=2)}\n\n"
        f"Answer:"
    )

    return genai_chat(client, prompt).strip()

def chat_with_public_ip_using_cached_result(question: str, tool_result: Dict[str, Any]) -> str:
    """
    Use OCI GenAI to answer a question using an ALREADY computed
    getPublicIpSummary JSON result (e.g., from cache).
    No MCP calls happen here.
    """
    client = get_genai_client()
    return answer_with_tool_result(client, question, "getPublicIpSummary", tool_result)

def chat_with_cost_using_cached_result(question: str, tool_result: Dict[str, Any]) -> str:
    """
    Use OCI GenAI to answer a cost-related question using an ALREADY computed
    getCostSummary JSON result (e.g., from cache).
    """
    client = get_genai_client()
    # We reuse the same answer_with_tool_result helper,
    # but pass the tool name "getCostSummary" so the prompt can mention "cost".
    return answer_with_tool_result(client, question, "getCostSummary", tool_result)

def chat_with_cloud_guard_using_cached_result(question: str, tool_result: Dict[str, Any]) -> str:
    """
    Use OCI GenAI to answer a Cloud Guard question using an ALREADY computed
    getCloudGuardSummary JSON result.
    """
    client = get_genai_client()
    return answer_with_tool_result(client, question, "getCloudGuardSummary", tool_result)



def chat_with_tenancy_assistant_oci(question: str) -> str:
    """
    Full flow:
    1. Ask OCI GenAI which tool (if any) to call.
    2. If tool is chosen, call MCP server.
    3. Ask OCI GenAI to answer based on the JSON result.
    """
    client = get_genai_client()

    # Step 1: decide tool
    decision = decide_tool_and_args(client, question)
    tool = decision.get("tool")
    args = decision.get("arguments") or {}

    if not tool:
        # No tool needed; just answer generically
        direct_system = """
You are an OCI Tenancy Assistant. The user will ask a question.
Answer based on your general knowledge about OCI.
If the question needs exact live tenancy data (counts, precise resource lists),
say you don't have direct data access in this mode.
"""
        prompt = f"{direct_system}\n\nUser question:\n{question}\n\nAnswer:"
        return genai_chat(client, prompt)

    if tool not in ("getPublicIpSummary", "getCostSummary", "getCloudGuardSummary"):
        raise RuntimeError(f"Unknown tool requested by model: {tool}")

    # Step 2: call MCP server with that tool
    tool_result = call_mcp_server(tool, args)

    # Step 3: get final natural language answer
    return answer_with_tool_result(client, question, tool, tool_result)


# =========================
# 4. MAIN
# =========================

def main():
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = input("Ask the OCI Tenancy Assistant a question: ")

    print(f"\n[QUESTION]\n{question}\n")

    try:
        answer = chat_with_tenancy_assistant_oci(question)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print("[ANSWER]\n")
    print(answer)
    print()


if __name__ == "__main__":
    main()
