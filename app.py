"""Brew Assistant — Streamlit chat UI.

Run: streamlit run app.py
"""
import contextlib
import io

import requests
import streamlit as st

import rag
from config import load_config
from ingest import main as ingest_main

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


def list_ollama_models() -> list[str]:
    """Model names from the Ollama server; empty list if unreachable."""
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=3)
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))
    except Exception:
        return []


def render_sources(sources: list[dict], scope: list[str] | None = None) -> None:
    """Render retrieved chunks for one answer."""
    if scope:
        st.caption("searched: " + ", ".join(scope))
    st.caption("Retrieved sources")
    for s in sources:
        with st.expander(f"[{s['type']}] {s['title']} — {s['source']}"):
            st.markdown(s["content"])


def main() -> None:
    st.set_page_config(page_title="Brew Assistant", page_icon="🍺", layout="wide")
    st.title("🍺 Brew Assistant")
    st.caption("Local RAG over brewing styles & ingredients — answers cite their sources.")

    cfg = load_config()

    # ---------- Sidebar ----------
    with st.sidebar:
        st.header("Settings")

        models = list_ollama_models()
        default_model = cfg["model"]
        if default_model not in models:
            models = [default_model] + models
        model = st.selectbox("Ollama model", models, index=models.index(default_model))

        temperature = st.slider(
            "Temperature", 0.0, 1.0, float(cfg["temperature"]), 0.05,
            help="Lower = more deterministic",
        )
        top_k = st.slider(
            "Top-K documents", 1, 8, int(cfg["top_k"]),
            help="How many chunks to retrieve per question",
        )
        counts = rag.type_counts()
        scope_options = rag.scope_options()
        if scope_options:
            scope = st.multiselect(
                "Search scope",
                scope_options,
                default=scope_options,
                format_func=lambda t: (
                    f"{rag.TYPE_LABELS.get(t, t.replace('_', ' ').title())} "
                    f"({counts.get(t, 0)})"
                ),
                help="Only retrieve these document types. Leave empty to search everything.",
            )
        else:
            scope = []
            st.caption("Search scope: run ingestion to enable type filtering.")
        show_sources = st.toggle("Show sources", value=True)

        st.divider()
        if st.button("🔄 Re-ingest data", use_container_width=True):
            with st.spinner("Re-ingesting documents into ChromaDB..."):
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf):
                        ingest_main()
                except SystemExit as e:
                    st.error(str(e))
                    return
                rag.reset_caches()  # drop stale store + type counts
            last_line = buf.getvalue().splitlines()[-1] if buf.getvalue() else "done"
            st.success(f"Re-ingested: {last_line}")

        if st.button("🗑️ Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.caption(f"Embedding: {cfg['embedding_model']} · ChromaDB: {cfg['chroma_dir'].name}/")

    # ---------- Chat history ----------
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and show_sources and msg.get("sources"):
                render_sources(msg["sources"], msg.get("scope"))

    # ---------- New question ----------
    if prompt := st.chat_input("Ask about beer styles, hops, malts, yeasts…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        history = [
            (m["role"], m["content"]) for m in st.session_state.messages[:-1]
        ]
        with st.chat_message("assistant"):
            docs = rag.retrieve(prompt, k=top_k, types=scope)
            sources = rag.docs_to_sources(docs)
            answer = st.write_stream(
                rag.stream_answer(
                    prompt, history, model=model, temperature=temperature, docs=docs
                )
            )
            if show_sources:
                render_sources(sources, scope)

        st.session_state.messages.append(
            {"role": "assistant", "content": answer, "sources": sources, "scope": scope}
        )


if __name__ == "__main__":
    main()
