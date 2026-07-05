#!/usr/bin/env python3
"""
Agentic Resume Builder
=======================
An agent that tailors your resume to a specific job posting using a
multi-step pipeline built on the Anthropic API:

  1. EXTRACT   - Analyze the job posting and pull out required skills,
                 keywords, and the core focus of the role.
  2. TAILOR    - Rewrite/reorder your existing resume content to align
                 with those requirements. It is instructed to ONLY use
                 facts already present in your original resume.
  3. VERIFY    - A separate "fact-check" pass compares the tailored
                 resume against the original and flags anything that
                 looks fabricated or unsupported.
  4. REVISE    - If the verify step finds issues, the agent regenerates
                 the tailored resume with the issues called out
                 (loops up to MAX_REVISIONS times).
  5. RENDER    - The final, verified JSON resume is rendered into a
                 clean, single-page PDF using reportlab.

This is "agentic" in that the script itself decides what to do at each
step based on the model's own output (e.g. whether to loop back and
revise), rather than just doing one prompt -> one response.

USAGE:
    export ANTHROPIC_API_KEY="your-key-here"
    python agentic_resume_builder.py \
        --resume my_resume.txt \
        --job job_posting.txt \
        --output tailored_resume.pdf

Resume input can be .txt, .pdf, or .docx.
Job posting input can be .txt or pasted via --job-text "...".
"""

import argparse
import json
import os
import sys
import re

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
MAX_REVISIONS = 2


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def load_resume_text(path: str) -> str:
    """Load resume content from .txt, .pdf, or .docx."""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".txt":
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if ext == ".docx":
        import docx
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs)

    raise ValueError(f"Unsupported resume file type: {ext}")


# ---------------------------------------------------------------------------
# Agent step helpers
# ---------------------------------------------------------------------------

def call_claude_json(client: Anthropic, system: str, user: str) -> dict:
    """Call Claude and parse a JSON object from its response.
    Strips markdown code fences if present."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# Step 1: Extract job requirements
# ---------------------------------------------------------------------------

def extract_requirements(client: Anthropic, job_text: str) -> dict:
    system = (
        "You are a precise job-posting analyst. Extract structured "
        "requirements from job postings. Respond with ONLY a JSON object, "
        "no preamble, no markdown fences."
    )
    user = f"""Analyze this job posting and extract:
- "must_have_skills": list of required technical skills/tools
- "nice_to_have_skills": list of preferred/bonus skills
- "keywords": list of important terms an ATS system would scan for
  (include exact phrasing from the posting where possible)
- "role_focus": one sentence describing what this role is really about
- "seniority_signal": one short phrase on the experience level expected

JOB POSTING:
{job_text}

Respond with ONLY the JSON object."""
    return call_claude_json(client, system, user)


# ---------------------------------------------------------------------------
# Step 2: Tailor resume content
# ---------------------------------------------------------------------------

RESUME_SCHEMA_NOTE = """
Return ONLY a JSON object with this exact structure:
{
  "name": "...",
  "contact": "phone | location | email | linkedin (whatever original has)",
  "summary": "2-3 sentence professional summary tailored to the role",
  "education": [
    {"degree": "...", "school": "...", "location": "...", "dates": "..."}
  ],
  "skills": [
    {"category": "...", "items": "comma-separated skills"}
  ],
  "experience": [
    {"title": "...", "org": "...", "dates": "...", "bullets": ["...", "..."]}
  ],
  "projects": [
    {"name": "...", "bullets": ["...", "..."]}
  ],
  "certifications": ["...", "..."]
}
"""


def tailor_resume(client: Anthropic, resume_text: str, requirements: dict,
                   revision_notes: str = "") -> dict:
    system = (
        "You are an expert resume writer. You tailor resumes to job "
        "postings by REWORDING, REORDERING, and RE-EMPHASIZING content "
        "that already exists in the candidate's original resume. "
        "CRITICAL RULE: Do not invent, exaggerate, or add any skill, "
        "tool, metric, employer, degree, or accomplishment that is not "
        "present in the original resume. If the posting wants something "
        "the candidate doesn't have, simply don't claim it. "
        "Respond with ONLY a JSON object, no preamble, no markdown fences."
    )

    revision_block = ""
    if revision_notes:
        revision_block = f"""
IMPORTANT - A fact-checking pass found issues with your last draft.
Fix these before responding again:
{revision_notes}
"""

    user = f"""ORIGINAL RESUME (source of truth - only use facts from here):
{resume_text}

JOB REQUIREMENTS EXTRACTED FROM POSTING:
{json.dumps(requirements, indent=2)}
{revision_block}
Tailor the resume to this posting:
- Rewrite the summary to speak directly to the role_focus and keywords.
- Reorder skills so the most relevant categories/items come first.
- Reword experience/project bullets to surface language and keywords
  from the posting WHERE THE UNDERLYING WORK ALREADY SUPPORTS IT.
- Do not fabricate anything not in the original resume.

{RESUME_SCHEMA_NOTE}
Respond with ONLY the JSON object."""
    return call_claude_json(client, system, user)


# ---------------------------------------------------------------------------
# Step 3: Verify no fabrication
# ---------------------------------------------------------------------------

def verify_resume(client: Anthropic, resume_text: str, tailored: dict) -> dict:
    system = (
        "You are a strict fact-checker comparing a tailored resume "
        "against the candidate's original resume. Flag ANY claim in the "
        "tailored version (skill, tool, metric, accomplishment, degree, "
        "employer, certification) that is not supported by the original. "
        "Respond with ONLY a JSON object, no preamble, no markdown fences."
    )
    user = f"""ORIGINAL RESUME:
{resume_text}

TAILORED RESUME (JSON):
{json.dumps(tailored, indent=2)}

Respond with ONLY this JSON:
{{
  "has_issues": true/false,
  "issues": ["specific unsupported claim 1", "specific unsupported claim 2"]
}}"""
    return call_claude_json(client, system, user)


# ---------------------------------------------------------------------------
# Step 3.5: Draft cover letter
# ---------------------------------------------------------------------------

def draft_cover_letter(client: Anthropic, resume_text: str,
                       requirements: dict, job_text: str,
                       company: str = "") -> dict:
    system = (
        "You are an expert cover letter writer. You write concise, "
        "specific, professional cover letters (250-350 words). "
        "CRITICAL RULE: Only reference skills, projects, and experience "
        "that appear in the candidate's resume. Never invent anything. "
        "Avoid cliches like 'I am writing to express my interest'. "
        "Open with something substantive. "
        "Respond with ONLY a JSON object, no preamble, no markdown fences."
    )

    if company:
        company_instruction = (
            f"The hiring company is: {company}. Address the letter to "
            f"'Hiring Team, {company}' and refer to {company} as the "
            f"employer throughout. Do NOT treat any tool or platform "
            f"mentioned in the posting as the employer."
        )
        recipient_hint = f"Hiring Team, {company}"
    else:
        company_instruction = (
            "Identify the hiring company from the posting. IMPORTANT: "
            "tools, software, or platforms mentioned as things the "
            "candidate would USE (e.g. 'platforms like X', 'experiment "
            "with X') are NOT the employer. If no employer name is "
            "clearly stated, use 'Hiring Team' with no company name "
            "rather than guessing."
        )
        recipient_hint = "Hiring Team, Company Name (only if clearly stated; otherwise just 'Hiring Team')"

    user = f"""CANDIDATE RESUME (source of truth):
{resume_text}

JOB REQUIREMENTS:
{json.dumps(requirements, indent=2)}

FULL JOB POSTING (for role title / tone):
{job_text[:3000]}

COMPANY GUIDANCE:
{company_instruction}

Write a tailored cover letter. Respond with ONLY this JSON:
{{
  "candidate_name": "...",
  "contact_line": "phone | location | email",
  "date": "leave as empty string",
  "recipient": "{recipient_hint}",
  "salutation": "Dear ...,",
  "paragraphs": ["opening paragraph", "body paragraph 1", "body paragraph 2", "closing paragraph"],
  "closing": "Sincerely,"
}}"""
    return call_claude_json(client, system, user)


def render_cover_letter_pdf(letter: dict, output_path: str) -> None:
    from datetime import date as _date
    from reportlab.lib.pagesizes import letter as letter_size
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "CLName", parent=styles["Title"], fontSize=16, alignment=TA_CENTER,
        spaceAfter=2,
    )
    contact_style = ParagraphStyle(
        "CLContact", parent=styles["Normal"], fontSize=9.5,
        alignment=TA_CENTER, textColor="#333333", spaceAfter=18,
    )
    body_style = ParagraphStyle(
        "CLBody", parent=styles["Normal"], fontSize=10.5, leading=15,
        spaceAfter=10,
    )

    doc = SimpleDocTemplate(
        output_path, pagesize=letter_size,
        topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
    )
    story = []
    story.append(Paragraph(letter.get("candidate_name", ""), name_style))
    story.append(Paragraph(letter.get("contact_line", ""), contact_style))

    letter_date = letter.get("date") or _date.today().strftime("%B %d, %Y")
    story.append(Paragraph(letter_date, body_style))
    if letter.get("recipient"):
        story.append(Paragraph(letter["recipient"], body_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(letter.get("salutation", "Dear Hiring Manager,"), body_style))

    for para in letter.get("paragraphs", []):
        story.append(Paragraph(para, body_style))

    story.append(Spacer(1, 6))
    story.append(Paragraph(letter.get("closing", "Sincerely,"), body_style))
    story.append(Paragraph(letter.get("candidate_name", ""), body_style))

    doc.build(story)


# ---------------------------------------------------------------------------
# Step 4: Render to PDF
# ---------------------------------------------------------------------------

def render_pdf(resume: dict, output_path: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable, ListFlowable, ListItem
    )

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "NameStyle", parent=styles["Title"], fontSize=18, alignment=TA_CENTER,
        spaceAfter=2,
    )
    contact_style = ParagraphStyle(
        "ContactStyle", parent=styles["Normal"], fontSize=9.5,
        alignment=TA_CENTER, textColor="#333333", spaceAfter=6,
    )
    section_style = ParagraphStyle(
        "SectionStyle", parent=styles["Heading2"], fontSize=11,
        spaceBefore=7, spaceAfter=3, textColor="#1a1a1a",
    )
    body_style = ParagraphStyle(
        "BodyStyle", parent=styles["Normal"], fontSize=9.3, leading=12,
    )
    entry_title_style = ParagraphStyle(
        "EntryTitle", parent=styles["Normal"], fontSize=9.8, leading=12,
        spaceBefore=3,
    )
    bullet_style = ParagraphStyle(
        "BulletStyle", parent=styles["Normal"], fontSize=9.1, leading=11.5,
        leftIndent=12, spaceAfter=1,
    )

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        topMargin=0.32 * inch, bottomMargin=0.3 * inch,
        leftMargin=0.55 * inch, rightMargin=0.55 * inch,
    )
    story = []

    story.append(Paragraph(resume.get("name", ""), name_style))
    story.append(Paragraph(resume.get("contact", ""), contact_style))
    story.append(HRFlowable(width="100%", color="#888888", thickness=0.75))

    if resume.get("summary"):
        story.append(Paragraph("PROFESSIONAL SUMMARY", section_style))
        story.append(Paragraph(resume["summary"], body_style))

    if resume.get("education"):
        story.append(Paragraph("EDUCATION", section_style))
        for edu in resume["education"]:
            degree = edu.get("degree", "").strip()
            school = edu.get("school", "").strip()
            location = edu.get("location", "").strip()
            dates = edu.get("dates", "").strip()
            school_part = ", ".join(p for p in [school, location] if p)
            line = f"<b>{degree}</b>"
            if school_part:
                line += f" &nbsp;|&nbsp; {school_part}"
            if dates:
                line += f" &nbsp;—&nbsp; {dates}"
            story.append(Paragraph(line, entry_title_style))

    if resume.get("skills"):
        story.append(Paragraph("TECHNICAL SKILLS", section_style))
        for skill_group in resume["skills"]:
            line = f"<b>{skill_group.get('category','')}:</b> {skill_group.get('items','')}"
            story.append(Paragraph(line, body_style))

    if resume.get("experience"):
        story.append(Paragraph("EXPERIENCE", section_style))
        for job in resume["experience"]:
            title = job.get("title", "").strip()
            org = job.get("org", "").strip()
            dates = job.get("dates", "").strip()
            title_line = f"<b>{title}</b>"
            if org:
                title_line += f" — {org}"
            if dates:
                title_line += f" &nbsp;<i>({dates})</i>"
            story.append(Paragraph(title_line, entry_title_style))
            bullets = [ListItem(Paragraph(b, bullet_style), leftIndent=12) for b in job.get("bullets", [])]
            if bullets:
                story.append(ListFlowable(bullets, bulletType="bullet", start="•", leftIndent=10))

    if resume.get("projects"):
        story.append(Paragraph("PROJECTS", section_style))
        for proj in resume["projects"]:
            story.append(Paragraph(f"<b>{proj.get('name','')}</b>", entry_title_style))
            bullets = [ListItem(Paragraph(b, bullet_style), leftIndent=12) for b in proj.get("bullets", [])]
            if bullets:
                story.append(ListFlowable(bullets, bulletType="bullet", start="•", leftIndent=10))

    if resume.get("certifications"):
        story.append(Paragraph("CERTIFICATIONS", section_style))
        for cert in resume["certifications"]:
            story.append(Paragraph(f"• {cert}", body_style))

    doc.build(story)


# ---------------------------------------------------------------------------
# Orchestration (the "agentic loop")
# ---------------------------------------------------------------------------

def run_agent(resume_path: str, job_text: str, output_path: str,
              cover_letter: bool = True, company: str = "", verbose=True):
    client = Anthropic()  # reads ANTHROPIC_API_KEY from env

    resume_text = load_resume_text(resume_path)

    if verbose:
        print("[1/5] Extracting requirements from job posting...")
    requirements = extract_requirements(client, job_text)
    if verbose:
        print(f"      -> Role focus: {requirements.get('role_focus')}")
        print(f"      -> Must-have skills: {', '.join(requirements.get('must_have_skills', []))}")

    if verbose:
        print("[2/5] Drafting tailored resume...")
    tailored = tailor_resume(client, resume_text, requirements)

    revision_notes = ""
    for attempt in range(1, MAX_REVISIONS + 1):
        if verbose:
            print(f"[3/5] Fact-checking draft (pass {attempt})...")
        check = verify_resume(client, resume_text, tailored)
        if not check.get("has_issues"):
            if verbose:
                print("      -> No unsupported claims found.")
            break
        if verbose:
            print(f"      -> Found {len(check.get('issues', []))} issue(s), revising...")
        revision_notes = "\n".join(f"- {i}" for i in check.get("issues", []))
        tailored = tailor_resume(client, resume_text, requirements, revision_notes)
    else:
        if verbose:
            print("      -> Max revisions reached; proceeding with last draft.")

    letter = None
    if cover_letter:
        if verbose:
            print("[4/5] Drafting cover letter...")
        letter = draft_cover_letter(client, resume_text, requirements, job_text, company)

    if verbose:
        print("[5/5] Rendering PDFs...")
    render_pdf(tailored, output_path)
    if verbose:
        print(f"      -> Resume saved to {output_path}")

    if letter:
        cl_path = os.path.splitext(output_path)[0] + "_cover_letter.pdf"
        render_cover_letter_pdf(letter, cl_path)
        if verbose:
            print(f"      -> Cover letter saved to {cl_path}")

    if verbose:
        print("Done.")

    return tailored, letter


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Agentic resume tailoring tool")
    parser.add_argument("--resume", required=True, help="Path to your resume (.txt, .pdf, .docx)")
    job_group = parser.add_mutually_exclusive_group(required=True)
    job_group.add_argument("--job", help="Path to job posting text file")
    job_group.add_argument("--job-text", help="Job posting text pasted directly")
    parser.add_argument("--output", default="tailored_resume.pdf", help="Output PDF path")
    parser.add_argument("--no-cover-letter", action="store_true",
                        help="Skip cover letter generation")
    parser.add_argument("--company", default="",
                        help="Employer name for the cover letter (recommended when the posting doesn't clearly name the company)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: Set the ANTHROPIC_API_KEY environment variable first.")
        print('  export ANTHROPIC_API_KEY="your-key-here"')
        sys.exit(1)

    job_text = args.job_text
    if args.job:
        with open(args.job, "r", encoding="utf-8") as f:
            job_text = f.read()

    run_agent(args.resume, job_text, args.output,
              cover_letter=not args.no_cover_letter,
              company=args.company)


if __name__ == "__main__":
    main()
