"""Thin Streamlit chat UI. All agent/tool logic lives in the FastAPI backend (backend/app/) -
this file only sends messages and renders the response. Run: streamlit run ui/main.py"""
import json
import os
import uuid

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

st.set_page_config(page_title="databench — data agent", page_icon="📊")


def init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []  # [{"role", "content", "tools": [(name, result)]}]
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())


def render_tools(tools: list) -> None:
    for name, result in tools:
        with st.expander(f"🔧 {name}"):
            st.code(result, language="text")


def send_message(prompt: str, placeholder) -> tuple[str, list]:
    answer = ""
    tools: list[tuple[str, str]] = []
    with httpx.stream(
        "POST",
        f"{BACKEND_URL}/chat/stream",
        json={"session_id": st.session_state.session_id, "message": prompt},
        timeout=120,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event["type"] == "text":
                answer += event["text"]
                placeholder.markdown(answer + "▌")
            elif event["type"] == "tool_call":
                tools.append((event["name"], event["result"]))
    placeholder.markdown(answer)
    return answer, tools


init_state()

with st.sidebar:
    st.header("Connection")
    try:
        httpx.get(f"{BACKEND_URL}/health", timeout=3).raise_for_status()
        st.write("🟢 backend reachable")
    except Exception as e:
        st.write(f"🔴 backend unreachable: {type(e).__name__}")
    st.code(BACKEND_URL, language="text")
    if st.button("New chat"):
        httpx.delete(f"{BACKEND_URL}/chat/{st.session_state.session_id}", timeout=5)
        st.session_state.clear()
        st.rerun()

st.title("📊 databench — data agent")
st.caption("Ask questions about the files and database tables it has access to.")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        render_tools(m.get("tools", []))

if prompt := st.chat_input("Ask something (try: what data sources are available?)"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        placeholder = st.empty()
        try:
            answer, tools = send_message(prompt, placeholder)
            render_tools(tools)
        except Exception as e:
            answer, tools = f"⚠️ Request failed: `{type(e).__name__}: {e}`", []
            placeholder.error(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer, "tools": tools})
