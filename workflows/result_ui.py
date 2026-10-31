"""Compact result presentation; source responses remain unchanged."""
import re

import streamlit as st


INTERNAL_HEADINGS = {
    "RAG检索依据", "检索依据", "内部防幻觉警告", "内部风险提示",
    "知识库引用溯源", "知识库引用", "知识库溯源",
}


def _internal_heading(line):
    title = line.strip()
    title = re.sub(r"^#{1,6}\s*", "", title)
    title = re.sub(r"^(?:\d+[.、)）]|[一二三四五六七八九十]+[、.])\s*", "", title)
    title = title.strip(" *#_：:【】[]（）()")
    title = re.sub(r"\s+", "", title)
    return title if title in INTERNAL_HEADINGS else None


def split_result_content(content):
    """Fold only explicit internal headings, leaving ordinary replies intact."""
    sections = []
    lines = []
    internal = False
    fence = None
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
            lines.append(line)
            continue
        heading = _internal_heading(line) if fence is None else None
        # A subsequent Markdown/bracket heading starts a new visible section.
        visible_heading = fence is None and bool(re.match(
            r"^(?:#{1,6}\s+|\*\*[^*]+\*\*\s*[:：]?\s*$|【[^】]+】\s*$)", stripped))
        if heading or (internal and visible_heading):
            if lines:
                sections.append((internal, "".join(lines)))
            lines = []
            internal = bool(heading)
        lines.append(line)
    if lines:
        sections.append((internal, "".join(lines)))
    return sections


def render_generation_result(response):
    sections = split_result_content(response.content)
    for internal, text in sections:
        if not internal:
            st.write(text)
    internal_sections = [text for internal, text in sections if internal]
    if internal_sections or response.risks or response.pending_confirmations:
        with st.expander("检索依据、内部警告与知识库引用溯源", expanded=False):
            for text in internal_sections:
                st.write(text)
            if response.risks:
                st.markdown("**内部防幻觉警告**")
                for risk in response.risks:
                    st.warning(risk.message)
            if response.pending_confirmations:
                st.markdown("**知识库引用溯源与待确认事项**")
                for note in response.pending_confirmations:
                    st.write(note)
