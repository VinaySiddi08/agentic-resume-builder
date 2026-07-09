# Agentic Resume Builder

![Agentic Resume Builder](screenshot.png)

An AI agent that tailors your resume to a specific job posting, fact-checks its
own output so it never invents experience you don't have, and drafts a matching
cover letter. Roughly a minute per application.

## What makes it an agent

Most AI resume tools send one prompt and print whatever comes back. This one
runs a pipeline where each step decides what happens next — including a
verification loop that catches and repairs its own mistakes:

1. **Extract** — reads the job posting and pulls out required skills, ATS
   keywords, and the real focus of the role.
2. **Tailor** — rewrites the summary, reorders skills, and rewords bullet
   points to speak to those requirements, using only facts already present
   in your original resume.
3. **Verify** — a separate fact-checking pass compares the tailored draft
   against your original and flags any claim it can't support.
4. **Revise (loop)** — if issues are found, the agent regenerates the draft
   with those issues called out, and re-checks. Up to two rounds.
5. **Render** — writes the verified content into a clean one-page PDF, plus
   a cover letter.

In practice step 3 earns its keep. On a mismatched posting it routinely catches
half a dozen overstated claims in the first draft and rewrites them out.
The tool repositions what is true; it does not fabricate.

## Requirements

- Python 3.10 or newer
- An [Anthropic API](https://console.anthropic.com) account with some credit

**Bring your own API key.** Anthropic bills whichever key makes the request,
so running this uses your own account. A full run (resume + cover letter)
costs a few cents. Your key is read from the environment or entered into a
masked field, used in memory for that one request, and never written to disk
or logged. Never commit a key — the included `.gitignore` helps.

## Install

```bash
git clone https://github.com/VinaySiddi08/agentic-resume-builder.git
cd agentic-resume-builder
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

On Windows, use `set ANTHROPIC_API_KEY=...` instead of `export`.
The `export` lasts only for that terminal session.

## Use it

### Web interface (easiest — no setup files needed)

```bash
streamlit run app.py
```

Opens in your browser. Upload your resume, paste the job posting, enter the
company name, click **Generate**, and download both PDFs. The agent's progress
is shown live as it works through each step.

If `ANTHROPIC_API_KEY` isn't set, the sidebar will ask for one.

### Command line

The CLI reads your resume from a file. Copy the example and fill in your own
details:

```bash
cp resume_source.example.txt resume_source.txt
```

`resume_source.txt` is git-ignored, so your personal information stays local.
Then save a job posting to `job_posting.txt` and run:

```bash
python agentic_resume_builder.py \
    --resume resume_source.txt \
    --job job_posting.txt \
    --company "Acme Corp" \
    --output Resume_Acme.pdf
```

That produces two files: `Resume_Acme.pdf` and
`Resume_Acme_cover_letter.pdf`.

| Flag | Purpose |
| --- | --- |
| `--resume` | Your resume: `.txt`, `.pdf`, or `.docx` |
| `--job` / `--job-text` | Posting from a file, or pasted inline |
| `--company` | Employer name (see below) |
| `--output` | Output PDF path |
| `--no-cover-letter` | Skip the cover letter |

### Naming the employer

Many postings never state the hiring company in the description, or they
mention tools the company uses — which an agent can mistake for the employer.
Passing `--company` (or filling the field in the web UI) avoids a cover letter
addressed to the wrong organization.

Leave it off and the agent will only name a company when the posting clearly
states one, falling back to a neutral "Hiring Team" rather than guessing.

## Known limitations

- **Scanned-image PDFs won't work.** If your resume PDF has no embedded text
  layer, nothing can be extracted. Use a `.docx` or `.txt` instead.
- **The layout targets one page.** A long career history may overflow onto a
  second page. Edit `render_pdf()` to adjust spacing — it's plain `reportlab`.
- **It cannot judge whether you're eligible.** Some postings (US civil service
  roles especially) screen against a rigid education-and-years formula. The
  agent will honestly present what you have; confirming you meet a stated
  minimum qualification is on you.
- **Always read the output before sending.** The fact-checker prevents
  fabrication, not awkward emphasis. You are the final editor.

## Files

| File | Purpose |
| --- | --- |
| `agentic_resume_builder.py` | The agent and CLI |
| `app.py` | Streamlit web interface |
| `resume_source.example.txt` | Template to copy for CLI use |
| `requirements.txt` | Python dependencies |

## License

MIT — do what you like with it.
