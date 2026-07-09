"""
Streamlit UI for the Agentic Resume Builder.

Run with:
    streamlit run app.py

The API key is read from the ANTHROPIC_API_KEY environment variable if set.
Otherwise the app asks for it in a masked input. The key is held in memory
for the duration of the request only. It is never written to disk or logged.
"""

import os
import tempfile
from pathlib import Path

import streamlit as st
from anthropic import Anthropic

from agentic_resume_builder import (
    load_resume_text,
    extract_requirements,
    tailor_resume,
    verify_resume,
    draft_cover_letter,
    render_pdf,
    render_cover_letter_pdf,
    MAX_REVISIONS,
)

st.set_page_config(page_title="Agentic Resume Builder", page_icon="📄", layout="centered")

st.title("Agentic Resume Builder")
st.caption(
    "Tailors your resume to a job posting, fact-checks itself against your "
    "original so nothing gets fabricated, and drafts a matching cover letter."
)

# --------------------------------------------------------------------------
# API key: environment variable first, masked input as fallback
# --------------------------------------------------------------------------
env_key = os.environ.get("ANTHROPIC_API_KEY", "")

with st.sidebar:
    st.header("Setup")
    if env_key:
        st.success("API key loaded from environment.")
        api_key = env_key
    else:
        st.info(
            "No `ANTHROPIC_API_KEY` found in your environment. "
            "Paste your key below — it is used only for this request and is "
            "never stored or logged."
        )
        api_key = st.text_input("Anthropic API key", type="password")
        st.caption("Get a key at console.anthropic.com")

    st.divider()
    make_cover_letter = st.checkbox("Also generate a cover letter", value=True)

# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
uploaded = st.file_uploader(
    "Your current resume", type=["pdf", "docx", "txt"],
    help="This is the source of truth. The agent will only use facts found here.",
)

job_text = st.text_area(
    "Job posting",
    height=220,
    placeholder="Paste the full job description here...",
)

company = st.text_input(
    "Company name (recommended)",
    placeholder="e.g. Health Research, Inc.",
    help="Many postings never name the employer, or mention tools that get "
         "mistaken for one. Naming it here avoids a misaddressed cover letter.",
)

generate = st.button("Generate", type="primary", use_container_width=True)

# --------------------------------------------------------------------------
# Run the pipeline
# --------------------------------------------------------------------------
if generate:
    if not api_key:
        st.error("An Anthropic API key is required.")
        st.stop()
    if uploaded is None:
        st.error("Please upload your resume.")
        st.stop()
    if not job_text.strip():
        st.error("Please paste the job posting.")
        st.stop()

    client = Anthropic(api_key=api_key)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Persist the upload so load_resume_text can read it by path
        resume_path = tmpdir / uploaded.name
        resume_path.write_bytes(uploaded.getvalue())

        try:
            resume_text = load_resume_text(str(resume_path))
        except Exception as e:
            st.error(f"Could not read that resume file: {e}")
            st.stop()

        if not resume_text.strip():
            st.error("No text could be extracted from that resume. If it is a "
                     "scanned image PDF, try a .docx or .txt version instead.")
            st.stop()

        status = st.status("Running the agent...", expanded=True)

        try:
            # 1. Extract
            status.write("**Extracting requirements** from the job posting...")
            requirements = extract_requirements(client, job_text)
            focus = requirements.get("role_focus", "")
            if focus:
                status.write(f"Role focus: {focus}")

            # 2. Tailor
            status.write("**Drafting** a tailored resume...")
            tailored = tailor_resume(client, resume_text, requirements)

            # 3. Verify / revise loop
            for attempt in range(1, MAX_REVISIONS + 1):
                status.write(f"**Fact-checking** the draft (pass {attempt})...")
                check = verify_resume(client, resume_text, tailored)
                if not check.get("has_issues"):
                    status.write("No unsupported claims found.")
                    break
                issues = check.get("issues", [])
                status.write(f"Found {len(issues)} unsupported claim(s) — revising...")
                notes = "\n".join(f"- {i}" for i in issues)
                tailored = tailor_resume(client, resume_text, requirements, notes)
            else:
                status.write("Max revisions reached; using the last draft.")

            # 4. Cover letter
            letter = None
            if make_cover_letter:
                status.write("**Drafting** the cover letter...")
                letter = draft_cover_letter(client, resume_text, requirements,
                                            job_text, company)

            # 5. Render
            status.write("**Rendering** PDFs...")
            slug = "".join(c for c in (company or "tailored") if c.isalnum()) or "tailored"
            resume_out = tmpdir / f"Resume_{slug}.pdf"
            render_pdf(tailored, str(resume_out))

            letter_bytes = None
            letter_name = None
            if letter:
                letter_out = tmpdir / f"CoverLetter_{slug}.pdf"
                render_cover_letter_pdf(letter, str(letter_out))
                letter_bytes = letter_out.read_bytes()
                letter_name = letter_out.name

            # Read bytes into memory BEFORE the temp dir is cleaned up, and
            # stash them in session_state so they survive Streamlit's reruns.
            # (Clicking a download button reruns the script from the top; if
            # the results only lived in local variables they would vanish.)
            st.session_state["results"] = {
                "resume_bytes": resume_out.read_bytes(),
                "resume_name": resume_out.name,
                "letter_bytes": letter_bytes,
                "letter_name": letter_name,
                "requirements": requirements,
            }

            status.update(label="Done.", state="complete", expanded=False)

        except Exception as e:
            status.update(label="Something went wrong.", state="error")
            st.error(f"{type(e).__name__}: {e}")
            st.stop()


# --------------------------------------------------------------------------
# Results — rendered outside the generate block so download buttons persist
# across reruns. Streamlit reruns the whole script on every button click.
# --------------------------------------------------------------------------
results = st.session_state.get("results")
if results:
    st.success("Your tailored documents are ready.")

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download resume (PDF)",
            data=results["resume_bytes"],
            file_name=results["resume_name"],
            mime="application/pdf",
            use_container_width=True,
        )
    if results["letter_bytes"]:
        with col2:
            st.download_button(
                "Download cover letter (PDF)",
                data=results["letter_bytes"],
                file_name=results["letter_name"],
                mime="application/pdf",
                use_container_width=True,
            )

    reqs = results["requirements"]
    with st.expander("What the agent picked up from the posting"):
        st.write("**Must-have skills**")
        st.write(", ".join(reqs.get("must_have_skills", [])) or "—")
        st.write("**Nice to have**")
        st.write(", ".join(reqs.get("nice_to_have_skills", [])) or "—")

    st.caption(
        "Read both documents before sending. The fact-checker prevents "
        "fabrication, but you are the final editor on tone and emphasis."
    )

    if st.button("Start over"):
        del st.session_state["results"]
        st.rerun()
