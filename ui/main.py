"""Thin Streamlit chat UI: logs in, sends messages, renders replies. Logic lives in the backend (backend/app/).
Run: streamlit run ui/main.py. The login lives in session state, so reloading the browser tab logs out."""
import base64
import json
import os

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

st.set_page_config(page_title="databench — data agent", page_icon="📊")


class BackendError(Exception):
    """The backend refused a request; the message is its own explanation, fit to show the user."""


def init_state() -> None:
    st.session_state.setdefault("token", None)
    st.session_state.setdefault("username", None)
    st.session_state.setdefault("chat_id", None)  # None: a new chat, created when its first question is sent
    # [{"role", "content", "tools": [(name, result)], "plots": [{"title", "png"}], "chart_summary", "errors", "failed", "interrupted"}]
    st.session_state.setdefault("messages", [])


def detail_of(r: httpx.Response) -> str:
    """The backend's explanation of an error: its `detail`, or the bare status if it gave none."""
    try:
        detail = r.json()["detail"]
    except Exception:
        return f"HTTP {r.status_code}"
    if isinstance(detail, list):  # request validation: one entry per bad field
        return "; ".join(str(d.get("msg", d)) for d in detail)
    return str(detail)


def log_out() -> None:
    st.session_state.clear()
    st.rerun()


def api(method: str, path: str, **kwargs) -> httpx.Response:
    """A request to the backend as the logged-in user. A 401 means the login ran out: back to the login screen."""
    kwargs.setdefault("timeout", 15)
    r = httpx.request(method, f"{BACKEND_URL}{path}", headers={"Authorization": f"Bearer {st.session_state.token}"}, **kwargs)
    if r.status_code == 401:
        log_out()
    return r


def authenticate(path: str, username: str, password: str) -> None:
    try:
        r = httpx.post(f"{BACKEND_URL}{path}", json={"username": username, "password": password}, timeout=30)
    except httpx.HTTPError as e:
        st.error(f"Couldn't reach the backend: {type(e).__name__}")
        return
    if r.status_code not in (200, 201):
        st.error(detail_of(r))
        return
    st.session_state.token, st.session_state.username = r.json()["access_token"], r.json()["username"]
    st.rerun()


def render_login() -> None:
    st.title("📊 databench — data agent")
    st.caption("Log in to chat with your own data. Every account has its own chats and its own files.")
    login_tab, register_tab = st.tabs(["Log in", "Register"])
    with login_tab, st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Log in"):
            authenticate("/auth/login", username, password)
    with register_tab, st.form("register"):
        username = st.text_input(
            "Username", key="register_username", help="3-30 characters: lowercase letters, digits and hyphens. It names your private file space."
        )
        password = st.text_input("Password", type="password", key="register_password", help="At least 8 characters.")
        if st.form_submit_button("Create account"):
            authenticate("/auth/register", username, password)


def to_ui_message(m: dict) -> dict:
    """A message as the backend stores it, in the shape the rest of this file renders."""
    if m["role"] == "user":
        return {"role": "user", "content": m["content"]}
    failed = m.get("failed", False)
    return {
        "role": "assistant",
        "content": f"⚠️ Request failed: `{m['content']}`" if failed else m["content"],
        "tools": [(t["name"], t["result"]) for t in m.get("tools", [])],
        "plots": [{"title": p["title"], "png": base64.b64decode(p["png_b64"])} for p in m.get("plots", [])],
        "chart_summary": m.get("chart_summary", ""),
        "errors": m.get("errors", []),
        "failed": failed,
        "interrupted": m.get("interrupted", False),
    }


def start_new_chat() -> None:
    st.session_state.chat_id, st.session_state.messages = None, []


def open_chat(chat_id: str) -> None:
    r = api("GET", f"/chats/{chat_id}")
    if r.status_code == 200:
        st.session_state.chat_id = chat_id
        st.session_state.messages = [to_ui_message(m) for m in r.json()["messages"]]
    else:  # deleted from another tab or session
        start_new_chat()
    st.rerun()


def render_chat_list() -> None:
    st.header("Chats")
    if st.button("➕ New chat", width="stretch"):
        start_new_chat()
        st.rerun()
    for chat in api("GET", "/chats").json():
        selected = chat["id"] == st.session_state.chat_id
        if st.button(chat["title"], key=f"chat_{chat['id']}", type="primary" if selected else "secondary", width="stretch"):
            open_chat(chat["id"])
    if st.session_state.chat_id:
        with st.popover("🗑 Delete this chat", width="stretch"):
            st.write("This removes the chat and its charts.")
            if st.button("Yes, delete it", key="confirm_delete_chat", type="primary"):
                api("DELETE", f"/chats/{st.session_state.chat_id}")
                start_new_chat()
                st.rerun()


def render_tools(tools: list) -> None:
    for name, result in tools:
        with st.expander(f"🔧 {name}"):
            st.code(result, language="text")


def render_file_manager() -> None:
    """Upload/list/delete against the backend's /files endpoints - the logged-in user's own files,
    which the data agent can then query."""
    st.header("Your files")
    try:
        files = api("GET", "/files").raise_for_status().json()
    except httpx.HTTPStatusError as e:
        st.caption(f"Couldn't list files: {e.response.status_code}")
        return
    except httpx.HTTPError as e:
        st.caption(f"Couldn't reach backend: {type(e).__name__}")
        return

    uploaded = st.file_uploader("Upload a csv/xlsx", type=["csv", "xlsx", "xls"])
    if uploaded is not None and st.button("Upload", key="upload_btn"):
        r = api("POST", "/files", files={"file": (uploaded.name, uploaded.getvalue())}, timeout=60)
        if r.status_code == 200:
            st.success(f"Uploaded {uploaded.name}")
            st.rerun()
        st.error(detail_of(r))

    if not files:
        st.caption("No files uploaded yet.")
        return

    selected = st.selectbox("Existing files", [f["name"] for f in files])
    if st.button("Delete selected", key="delete_btn"):
        api("DELETE", f"/files/{selected}")
        st.rerun()


def render_reply(m: dict) -> None:
    """A stored assistant message: the data agent's answer, then the statistician's chart(s) and
    summary (only present for results big enough to be charted), then the tool calls."""
    if m.get("failed"):
        st.error(m["content"])
    else:
        st.markdown(m["content"])
    for plot in m.get("plots", []):
        st.image(plot["png"], width="stretch")
    if m.get("chart_summary"):
        st.markdown(m["chart_summary"])
    for message in m.get("errors", []):
        st.warning(f"Chart unavailable: {message}")
    if m.get("interrupted"):
        st.caption("⏹ This answer was cut off.")
    render_tools(m.get("tools", []))


def send_message(prompt: str, container) -> dict:
    """Stream one turn into `container`. Elements are added in the order events arrive, so the
    answer streams first, then the chart appears and the chart summary streams below it."""
    reply = {"content": "", "tools": [], "plots": [], "chart_summary": "", "errors": [], "failed": False}
    answer_box = container.empty()
    summary_box = progress = None
    try:
        with httpx.stream(
            "POST",
            f"{BACKEND_URL}/chat/stream",
            json={"chat_id": st.session_state.chat_id, "message": prompt},
            headers={"Authorization": f"Bearer {st.session_state.token}"},
            timeout=120,
        ) as r:
            if r.status_code == 401:
                log_out()
            if r.status_code != 200:
                r.read()
                raise BackendError(detail_of(r))
            for line in r.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                if event["type"] == "chat":  # a new chat's id, before anything else
                    st.session_state.chat_id = event["chat_id"]
                elif event["type"] == "text" and event["agent"] == "data_agent":
                    reply["content"] += event["text"]
                    answer_box.markdown(reply["content"] + "▌")
                elif event["type"] == "text":  # the statistician's summary
                    summary_box = summary_box or container.empty()
                    reply["chart_summary"] += event["text"]
                    summary_box.markdown(reply["chart_summary"] + "▌")
                elif event["type"] == "tool_call":
                    reply["tools"].append((event["name"], event["result"]))
                elif event["type"] == "stage":
                    answer_box.markdown(reply["content"])
                    progress = container.empty()
                    progress.caption("📈 Building a chart...")
                elif event["type"] == "plot":
                    if progress is not None:
                        progress.empty()
                    reply["plots"].append({"title": event["title"], "png": base64.b64decode(event["png_b64"])})
                    container.image(reply["plots"][-1]["png"], width="stretch")
                elif event["type"] == "error" and event["agent"] == "data_agent":
                    reply["content"], reply["failed"] = f"⚠️ Request failed: `{event['message']}`", True
                elif event["type"] == "error":  # the statistician: the answer stands, only the chart is missing
                    reply["errors"].append(event["message"])
                    container.warning(f"Chart unavailable: {event['message']}")
    except BackendError as e:
        reply["content"], reply["failed"] = f"⚠️ {e}", True
    except Exception as e:
        reply["content"], reply["failed"] = f"⚠️ Request failed: `{type(e).__name__}: {e}`", True
    if progress is not None:
        progress.empty()
    if reply["failed"]:
        answer_box.error(reply["content"])
        return reply
    answer_box.markdown(reply["content"])
    if summary_box is not None:
        summary_box.markdown(reply["chart_summary"])
    return reply


init_state()

if not st.session_state.token:
    render_login()
    st.stop()

if "account_checked" not in st.session_state:  # a token can outlive its account (database reset)
    api("GET", "/auth/me")
    st.session_state.account_checked = True

with st.sidebar:
    st.caption(f"Signed in as **{st.session_state.username}**")
    if st.button("Log out"):
        log_out()

    st.divider()
    render_chat_list()

    st.divider()
    render_file_manager()

    st.divider()
    try:
        httpx.get(f"{BACKEND_URL}/health", timeout=3).raise_for_status()
        st.caption(f"🟢 backend reachable · {BACKEND_URL}")
    except Exception as e:
        st.caption(f"🔴 backend unreachable: {type(e).__name__} · {BACKEND_URL}")

st.title("📊 databench — data agent")
st.caption("Ask questions about your files and the shared sample data. Big results get a chart.")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m["role"] == "assistant":
            render_reply(m)
        else:
            st.markdown(m["content"])

if prompt := st.chat_input("Ask something (try: what data sources are available?)"):
    starting_new_chat = st.session_state.chat_id is None
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        container = st.container()
        reply = send_message(prompt, container)
        render_tools(reply["tools"])
    st.session_state.messages.append({"role": "assistant", **reply})
    if starting_new_chat and st.session_state.chat_id is not None:
        st.rerun()  # the sidebar was drawn before this chat existed
