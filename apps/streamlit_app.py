import time
from datetime import datetime, timezone
from pathlib import Path
import sys

import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tenancy_assistant.genai_assistant import (
    get_public_ip_summary,
    get_cost_summary,
    get_cloud_guard_summary,
    chat_with_public_ip_using_cached_result,
    chat_with_cost_using_cached_result,
    chat_with_cloud_guard_using_cached_result,
)

# --------- PAGE CONFIG & BASIC STYLE ---------
st.set_page_config(
    page_title="OCI Tenancy Assistant",
    page_icon="OCI",
    layout="wide",
)

# Custom CSS to make things look nicer
st.markdown(
    """
    <style>
    /* Global */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 1.5rem;
        max-width: 1200px;
    }

    /* Header gradient bar */
    .top-header {
        background: linear-gradient(90deg, #0f172a, #1d4ed8);
        color: white;
        padding: 1.2rem 1.5rem;
        border-radius: 0.75rem;
        margin-bottom: 1.2rem;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.3);
    }

    .top-header h1 {
        font-size: 1.6rem;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 0.6rem;
    }

    .top-header .subtitle {
        margin-top: 0.35rem;
        font-size: 0.95rem;
        opacity: 0.9;
    }

    /* Info cards */
    .info-card {
        background: #0f172a;
        color: #e5e7eb;
        padding: 0.9rem 1rem;
        border-radius: 0.75rem;
        border: 1px solid #1f2937;
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.5);
        font-size: 0.9rem;
    }

    .info-card h4 {
        margin: 0 0 0.35rem 0;
        font-size: 0.95rem;
        color: #bfdbfe;
    }

    .info-metric {
        display: flex;
        justify-content: space-between;
        margin-top: 0.25rem;
        font-size: 0.88rem;
    }

    .info-metric span.value {
        font-weight: 600;
        color: #e5e7eb;
    }

    /* Buttons */
    .stButton>button {
        border-radius: 999px;
        border: 1px solid rgba(148, 163, 184, 0.45);
        padding: 0.3rem 1.1rem;
        font-size: 0.9rem;
        font-weight: 500;
    }

    /* Chat tweaks */
    .stChatMessage {
        border-radius: 0.75rem;
        padding: 0.6rem 0.75rem;
    }

    .stChatMessage [data-testid="stMarkdownContainer"] p {
        margin-bottom: 0.4rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------- HEADER ---------
st.markdown(
    """
    <div class="top-header">
        <h1>OCI Tenancy Assistant</h1>
        <div class="subtitle">
            Natural-language insights on your OCI tenancy, powered by OCI Generative AI + a custom MCP-style wrapper.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --------- MODE SELECTION ---------
mode = st.radio(
    "View",
    options=["Public IPs", "Cost", "Cloud Guard"],
    horizontal=True,
    index=0,
)


# --------- CACHE HELPERS ---------

@st.cache_data(ttl=1800)  # 30 minutes cache
def get_cached_public_ip_summary():
    data = get_public_ip_summary({"scope": "ALL"})
    return {
        "data": data,
        "fetched_at": time.time(),
    }


@st.cache_data(ttl=1800)  # 30 minutes cache
def get_cached_cost_summary(time_start: str, time_end: str):
    data = get_cost_summary({
        "granularity": "MONTHLY",
        "group_by": "COMPARTMENT",
        "time_start": time_start,
        "time_end": time_end,
    })
    return {
        "data": data,
        "fetched_at": time.time(),
    }

@st.cache_data(ttl=1800)  # 30 minutes cache
def get_cached_cloud_guard_summary(include_endpoints: bool = True):
    data = get_cloud_guard_summary({
        "include_endpoints": include_endpoints,
        "max_problems": 10,
        "max_endpoints_per_problem": 10,
    })
    return {
        "data": data,
        "fetched_at": time.time(),
    }


# --------- SESSION STATE ---------
if "history" not in st.session_state:
    # history is per-mode: {"Public IPs": [...], "Cost": [...]}
    st.session_state.history = {"Public IPs": [], "Cost": [], "Cloud Guard": []}


# --------- LAYOUT: TWO COLUMNS ---------
left_col, right_col = st.columns([1.1, 2.2])

# --------- LEFT COLUMN: SNAPSHOT INFO + TIPS ---------
with left_col:
    if mode == "Public IPs":
        st.subheader("Public IP snapshot")

        if st.button("Preload public IP snapshot"):
            with st.spinner("Loading public IP summary from OCI (first call may take a while)..."):
                snapshot = get_cached_public_ip_summary()
            st.success("Public IP snapshot cached. Questions will now be faster.")

        try:
            snapshot = get_cached_public_ip_summary()
            summary = snapshot["data"]
            fetched_dt = datetime.fromtimestamp(snapshot["fetched_at"]).strftime("%Y-%m-%d %H:%M:%S")
            total_count = summary.get("total_count", "-")
            by_scope = summary.get("by_scope", {})
            eph = by_scope.get("EPHEMERAL", "-")
            resv = by_scope.get("RESERVED", "-")

            st.markdown(
                f"""
                <div class="info-card">
                    <h4>Current public IP snapshot</h4>
                    <div class="info-metric">
                        <span>Total public IPs</span>
                        <span class="value">{total_count}</span>
                    </div>
                    <div class="info-metric">
                        <span>Ephemeral</span>
                        <span class="value">{eph}</span>
                    </div>
                    <div class="info-metric">
                        <span>Reserved</span>
                        <span class="value">{resv}</span>
                    </div>
                    <div style="margin-top:0.55rem; font-size:0.8rem; opacity:0.8;">
                        Snapshot time: <code>{fetched_dt}</code> (approximate)
                    </div>
                    <div style="margin-top:0.35rem; font-size:0.78rem; opacity:0.75;">
                        Data is cached for ~10 minutes to keep responses fast.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.markdown(
                f"""
                <div class="info-card">
                    <h4>Current public IP snapshot</h4>
                    <div style="font-size:0.85rem;">
                        No snapshot available yet.<br/>
                        <span style="opacity:0.8;">Use the preload button above or just ask a question.</span>
                    </div>
                    <div style="margin-top:0.4rem; font-size:0.78rem; opacity:0.7;">
                        Technical detail: {e}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("### Tips for your demo (Public IPs)")
        st.markdown(
            """
            - Try asking:
              - *How many public IPs do I have?*
              - *How many are reserved vs ephemeral?*
              - *Summarize my public IP usage.*
            - Mention that the first call warms up a cached tenancy snapshot.
            - Explain that future tools (compute, storage, ATP, etc.) can plug into the same pattern.
            """
        )

    elif mode == "Cost":
        st.subheader("Cost snapshot")

        # ---- COST WINDOW PRESET SELECTOR ----
        preset = st.selectbox(
            "Cost window:",
            ["Current month", "Last full month"],
            index=0,
        )

        def get_date_range_for_preset(preset_name: str):
            now = datetime.now(timezone.utc)
            first_of_this_month = now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            if preset_name == "Last full month":
                # End = first day of this month (exclusive end)
                end = first_of_this_month

                # Start = first day of previous month
                if first_of_this_month.month == 1:
                    start = first_of_this_month.replace(
                        year=first_of_this_month.year - 1, month=12
                    )
                else:
                    start = first_of_this_month.replace(
                        month=first_of_this_month.month - 1, day=1
                    )
            else:  # "Current month"
                start = first_of_this_month
                end = now.replace(hour=0, minute=0, second=0, microsecond=0)

            return start.isoformat(), end.isoformat()

        # Compute time window based on user selection
        time_start, time_end = get_date_range_for_preset(preset)

        # ---- PRELOAD BUTTON ----
        if st.button("Preload cost snapshot"):
            with st.spinner("Loading cost summary from OCI Usage API (first call may take a while)..."):
                snapshot = get_cached_cost_summary(time_start, time_end)
            st.success("Cost snapshot cached. Cost questions will now be faster.")

        # ---- SHOW SNAPSHOT ----
        try:
            snapshot = get_cached_cost_summary(time_start, time_end)
            summary = snapshot["data"]
            fetched_dt = datetime.fromtimestamp(snapshot["fetched_at"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            total_cost = summary.get("total_cost", "-")
            currency = summary.get("currency", "").strip() or "USD"
            time_start_str = summary.get("time_start", "")
            time_end_str = summary.get("time_end", "")
            group_by = summary.get("group_by", "COMPARTMENT")
            items = summary.get("items", [])

            st.markdown(
                f"""
                <div class="info-card">
                    <h4>Cost snapshot ({preset})</h4>
                    <div class="info-metric">
                        <span>Total cost ({group_by.lower()})</span>
                        <span class="value">{currency} {total_cost}</span>
                    </div>
                    <div class="info-metric">
                        <span>Window</span>
                        <span class="value">{time_start_str[:10]} -> {time_end_str[:10]}</span>
                    </div>
                    <div style="margin-top:0.55rem; font-size:0.85rem;">
                        Top compartments:
                    </div>
                """
                + "".join(
                    f"""
                    <div class="info-metric">
                        <span>{item.get('label', item.get('key', 'UNKNOWN'))}</span>
                        <span class="value">{currency} {item.get('cost', 0)}</span>
                    </div>
                    """
                    for item in items[:3]
                )
                + f"""
                    <div style="margin-top:0.55rem; font-size:0.8rem; opacity:0.8;">
                        Snapshot time: <code>{fetched_dt}</code> (approximate)
                    </div>
                    <div style="margin-top:0.35rem; font-size:0.78rem; opacity:0.75;">
                        Data is cached for ~10 minutes to keep responses fast.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.markdown(
                f"""
                <div class="info-card">
                    <h4>Current cost snapshot</h4>
                    <div style="font-size:0.85rem;">
                        No snapshot available yet.<br/>
                        <span style="opacity:0.8;">Use the preload button above or just ask a cost-related question.</span>
                    </div>
                    <div style="margin-top:0.4rem; font-size:0.78rem; opacity:0.7;">
                        Technical detail: {e}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("### Tips for your demo (Cost)")
        st.markdown(
            """
            - Try asking:
              - *What is my total cost this month?*
              - *Give me a cost breakdown by compartment.*
              - *Which compartment is spending the most?*
            - Change the **Cost window** selector to show *Current month* vs *Last full month*.
            - Mention that data comes from OCI Usage API via your MCP cost tool.
            """
        )
    else:  # mode == "Cloud Guard"
        st.subheader("Cloud Guard snapshot")

        if st.button("Preload Cloud Guard snapshot"):
            with st.spinner("Loading Cloud Guard summary (first call may take a while)..."):
                snapshot = get_cached_cloud_guard_summary(include_endpoints=False)
            st.success("Cloud Guard snapshot cached. Questions will now be faster.")

        try:
            snapshot = get_cached_cloud_guard_summary(include_endpoints=False)
            summary = snapshot["data"]
            fetched_dt = datetime.fromtimestamp(snapshot["fetched_at"]).strftime("%Y-%m-%d %H:%M:%S")
            total_targets = summary.get("total_targets", "-")
            total_problems = summary.get("total_problems", "-")
            by_risk = summary.get("problems_by_risk", {})

            st.markdown(
                f"""
                <div class="info-card">
                    <h4>Cloud Guard snapshot</h4>
                    <div class="info-metric">
                        <span>Total targets</span>
                        <span class="value">{total_targets}</span>
                    </div>
                    <div class="info-metric">
                        <span>Total problems</span>
                        <span class="value">{total_problems}</span>
                    </div>
                    <div style="margin-top:0.55rem; font-size:0.85rem;">
                        Problems by risk:
                    </div>
                """
                + "".join(
                    f"""
                    <div class="info-metric">
                        <span>{risk}</span>
                        <span class="value">{count}</span>
                    </div>
                    """
                    for risk, count in sorted(by_risk.items())
                )
                + f"""
                    <div style="margin-top:0.55rem; font-size:0.8rem; opacity:0.8;">
                        Snapshot time: <code>{fetched_dt}</code> (approximate)
                    </div>
                    <div style="margin-top:0.35rem; font-size:0.78rem; opacity:0.75;">
                        Data is cached for ~10 minutes to keep responses fast.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.markdown(
                f"""
                <div class="info-card">
                    <h4>Cloud Guard snapshot</h4>
                    <div style="font-size:0.85rem;">
                        No snapshot available yet.<br/>
                        <span style="opacity:0.8;">Use the preload button above or just ask a question.</span>
                    </div>
                    <div style="margin-top:0.4rem; font-size:0.78rem; opacity:0.7;">
                        Technical detail: {e}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("### Tips for your demo (Cloud Guard)")
        st.markdown(
            """
            - Try asking:
              - *Show me Cloud Guard problems by risk level.*
              - *List Cloud Guard targets and their resource type.*
              - *Which problems have endpoints?*
            - Cloud Guard endpoints are pulled from recent problems (sampled).
            """
        )


# --------- RIGHT COLUMN: CHAT UI ---------
with right_col:
    st.subheader(f"Chat with your tenancy ({mode})")

    # Render existing messages for the current mode
    for msg in st.session_state.history.get(mode, []):
        with st.chat_message("user" if msg["role"] == "user" else "assistant"):
            st.markdown(msg["content"])

    # Chat input
    if mode == "Public IPs":
        placeholder = "Ask something like: How many public IPs do I have?"
    elif mode == "Cost":
        placeholder = "Ask something like: What is my total cost this month?"
    else:
        placeholder = "Ask something like: Show Cloud Guard problems by risk."
    user_input = st.chat_input(placeholder)

    if user_input:
        # Store & display user message
        st.session_state.history[mode].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Assistant message
        with st.chat_message("assistant"):
            with st.spinner(f"Thinking with OCI GenAI and cached {mode.lower()} data..."):
                try:
                    if mode == "Public IPs":
                        snapshot = get_cached_public_ip_summary()
                        tool_data = snapshot["data"]
                        answer = chat_with_public_ip_using_cached_result(
                            user_input,
                            tool_data,
                        )
                    elif mode == "Cost":
                        time_start, time_end = get_date_range_for_preset(preset)
                        snapshot = get_cached_cost_summary(time_start, time_end)
                        tool_data = snapshot["data"]
                        answer = chat_with_cost_using_cached_result(
                            user_input,
                            tool_data,
                        )
                    else:  # Cloud Guard
                        snapshot = get_cached_cloud_guard_summary(include_endpoints=True)
                        tool_data = snapshot["data"]
                        answer = chat_with_cloud_guard_using_cached_result(
                            user_input,
                            tool_data,
                        )
                except Exception as e:
                    answer = (
                        f"Sorry, something went wrong while querying {mode.lower()} data:\n\n"
                        f"`{e}`"
                    )

                st.markdown(answer)

        # Save assistant answer to history for this mode
        st.session_state.history[mode].append({"role": "assistant", "content": answer})
