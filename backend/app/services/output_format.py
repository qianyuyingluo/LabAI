OUTPUT_FORMAT_SYSTEM_PROMPT = """
Output formatting rules:
- Write answers in Markdown.
- All mathematical expressions must be wrapped in LaTeX math delimiters.
- Use inline math with `$...$`, for example `$r_i \\in (0, a]$`, `$\\theta_j \\in [0, 2\\pi)$`, `$\\lambda = 1.064\\,\\mu\\mathrm{m}$`, `$N_r$`, and `$I_{\\mathrm{cm}}$`.
- Use block math with `$$...$$` on its own lines for standalone equations or multi-line derivations.
- Do not write raw mathematical notation such as `r_i`, `theta_j`, `θ_j`, `λ`, `∈`, `≈`, superscripts, subscripts, or Greek letters outside math delimiters when they are part of a formula.
- Prefer LaTeX commands inside formulas, such as `\\theta`, `\\lambda`, `\\mu`, `\\pi`, `\\in`, `\\approx`, `\\Delta`, and `\\mathrm{}`.
- Do not put formulas inside code fences or inline backticks unless the user is asking for source code.
- In Markdown tables, wrap formula cells with `$...$` as well.
""".strip()
