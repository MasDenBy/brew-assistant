"""Brew Assistant — Streamlit chat UI.

Run: streamlit run app.py
"""
import contextlib
import io

import requests
import streamlit as st

import rag
from config import load_config, model_settings
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


def model_choices(installed: list[str], cfg: dict) -> list[str]:
    """Dropdown candidates, in order.

    With `models:` in config.yaml: its keys first (pulled before unpulled, both
    in config order — the row order is the user's shortlist), then any other
    installed models. Unpulled candidates stay visible so selecting one
    surfaces Ollama's clear error instead of silently hiding it. The default
    (`model:`) is always present for selection.

    Without `models:` this is the previous behavior: every installed model,
    config default inserted first.
    """
    listed = list(cfg.get("models") or {})
    if not listed:
        names = list(installed)
        if cfg["model"] not in names:
            names.insert(0, cfg["model"])
        return names
    pulled = [name for name in listed if name in installed]
    missing = [name for name in listed if name not in installed]
    others = sorted(name for name in installed if name not in listed)
    names = pulled + missing + others
    if cfg["model"] not in names:
        names.insert(0, cfg["model"])
    return names


def last_model_of(messages: list[dict]) -> str | None:
    """Model that generated the most recent assistant message (or None)."""
    return next(
        (
            m.get("model")
            for m in reversed(messages)
            if m["role"] == "assistant" and m.get("model")
        ),
        None,
    )


def render_sources(sources: list[dict], scope: list[str] | None = None) -> None:
    """Render retrieved chunks for one answer.

    Entries are numbered to match the [N] inline citations: docs are numbered
    1..k by retrieval order, exactly like format_context() numbers the
    prompt's context blocks.
    """
    if scope:
        st.caption("searched: " + ", ".join(scope))
    st.caption("Retrieved sources — numbers match the [N] citations above")
    for i, s in enumerate(sources, 1):
        with st.expander(f"{i}. ({s['type']}) {s['title']} — {s['source']}"):
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
        names = model_choices(models, cfg)
        model = st.selectbox(
            "Ollama model",
            names,
            index=names.index(cfg["model"]),
            format_func=lambda n: n if n in models else f"{n} (not installed)",
        )
        if cfg.get("models"):
            pulled = [n for n in cfg["models"] if n in models]
            st.caption(
                f"{len(pulled)}/{len(cfg['models'])} candidates pulled into Ollama"
            )

        # Keyed per model: switching models recreates the widget, so the slider
        # re-defaults from that model's settings (per-model override > global).
        temperature = st.slider(
            "Temperature", 0.0, 1.0,
            float(model_settings(cfg, model)["temperature"]), 0.05,
            key=f"temperature_slider_{model}",
            help="Lower = more deterministic; defaults follow the model's config.yaml override",
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

        st.caption(
            f"Embedding: {cfg['embedding_model']} · ChromaDB: {cfg['chroma_dir'].name}/"
            f" · num_ctx: {int(model_settings(cfg, model)['num_ctx'])}"
        )

    # ---------- Chat history ----------
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                if msg.get("model"):
                    t = msg.get("temperature")
                    t_part = f" · T={t:g}" if isinstance(t, (int, float)) else ""
                    st.caption(f"model: {msg['model']}{t_part}")
                if show_sources and msg.get("sources") and not msg.get("refused"):
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
            prev_model = last_model_of(st.session_state.messages)
            if prev_model and prev_model != model:
                st.caption(
                    f"⚠️ switched from {prev_model} to {model} — history above was"
                    f" generated by {prev_model}"
                )
            docs = rag.retrieve(prompt, k=top_k, types=scope)
            sources = rag.docs_to_sources(docs)
            answer = st.write_stream(
                rag.stream_answer(
                    prompt, history, model=model, temperature=temperature, docs=docs
                )
            )
            t = float(temperature)
            st.caption(f"model: {model} · T={t:g}")
            # A refusal means the retrieved chunks did not contain the answer,
            # so showing them would be misleading — render nothing but the text.
            refused = rag.is_refusal(answer)
            if show_sources and not refused:
                render_sources(sources, scope)

        st.session_state.messages.append(
            {
                "role": "assistant", "content": answer, "sources": sources,
                "scope": scope, "model": model, "temperature": float(temperature),
                "refused": refused,
            }
        )


if __name__ == "__main__":
    main()
