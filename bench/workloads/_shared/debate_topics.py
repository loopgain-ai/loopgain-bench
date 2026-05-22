"""Debate / critique-revise prompts with rubric checks.

The W2 family asks the model to produce a short structured argument; a
'critic' (the second iteration onward) is asked to find specific flaws and
the 'writer' revises. The rubric awards points for concrete properties that
can be checked programmatically (citation present, counter-argument named,
word count in range), so error = (max_score - actual_score).

Like W1, the prompts are inline to keep the bench reproducible without
runtime downloads. Difficulty is moderate; the bench measures the
loop-dynamics (does the critic-writer rotation converge or oscillate?), not
raw argument quality.
"""

from __future__ import annotations

TOPICS = [
    {
        "name": "remote_work",
        "prompt": "Argue for or against the claim: 'Remote work makes engineering teams more productive.' Pick a side. 120-180 words. Include at least one specific cited study or report, and explicitly name the strongest counter-argument before rebutting it.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["study", "report", "research", "survey", "analysis", "data from"],
            "counter_keywords": ["however", "counter", "critics", "opponents", "concede", "admittedly", "granted"],
        },
    },
    {
        "name": "ai_regulation",
        "prompt": "Argue for or against the claim: 'AI development should be paused for 6 months to allow safety research to catch up.' 120-180 words. Cite at least one specific group or paper, and address the strongest opposing view.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["paper", "letter", "institute", "research", "report", "study"],
            "counter_keywords": ["however", "counter", "opposing", "critics", "but", "concede", "yet"],
        },
    },
    {
        "name": "open_source",
        "prompt": "Argue for or against the claim: 'Open-source AI models are more dangerous than closed proprietary ones.' 120-180 words. Cite at least one example model and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["llama", "mistral", "gpt", "claude", "gemini", "model", "release", "weights"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "though"],
        },
    },
    {
        "name": "four_day_week",
        "prompt": "Argue for or against the claim: 'A four-day work week should be the new standard for knowledge workers.' 120-180 words. Cite a specific trial or study and address the strongest opposing view.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["trial", "study", "research", "iceland", "u.k.", "uk", "experiment", "pilot"],
            "counter_keywords": ["however", "but", "critics", "opponents", "concede", "yet", "though"],
        },
    },
    {
        "name": "universal_basic_income",
        "prompt": "Argue for or against the claim: 'Universal basic income would reduce overall societal welfare.' 120-180 words. Cite a specific UBI pilot or paper and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["pilot", "stockton", "finland", "kenya", "study", "trial", "experiment"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "though"],
        },
    },
    {
        "name": "nuclear_energy",
        "prompt": "Argue for or against the claim: 'Nuclear energy is essential to decarbonizing the global grid by 2050.' 120-180 words. Cite at least one specific report or agency and name the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["iea", "ipcc", "report", "agency", "study", "iaea", "doe"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
    {
        "name": "social_media_minors",
        "prompt": "Argue for or against the claim: 'Social media platforms should be banned for users under 16.' 120-180 words. Cite a specific study or jurisdiction's policy, and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["study", "australia", "research", "report", "haidt", "twenge", "policy"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
    {
        "name": "cities_cars",
        "prompt": "Argue for or against the claim: 'Major cities should ban private car ownership in city centres by 2035.' 120-180 words. Cite a specific city's policy or transportation study; name the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["amsterdam", "paris", "oslo", "study", "policy", "report", "transit", "transport"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
    {
        "name": "lab_grown_meat",
        "prompt": "Argue for or against the claim: 'Lab-grown meat will displace conventional livestock farming by 2045.' 120-180 words. Cite a specific company or research forecast, and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["upside", "eat just", "mosa", "forecast", "study", "research", "report", "fda", "usda"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
    {
        "name": "space_mining",
        "prompt": "Argue for or against the claim: 'Asteroid mining will be commercially viable before 2040.' 120-180 words. Cite a specific company or feasibility analysis, and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["psyche", "astroforge", "nasa", "study", "analysis", "report", "feasibility"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "skeptics"],
        },
    },
    {
        "name": "code_review",
        "prompt": "Argue for or against the claim: 'AI-assisted code review will fully replace human reviewers by 2030.' 120-180 words. Cite a specific tool or empirical study, and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["copilot", "study", "research", "report", "github", "survey", "tool"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "though"],
        },
    },
    {
        "name": "ev_subsidy",
        "prompt": "Argue for or against the claim: 'Government EV subsidies are regressive and should be phased out.' 120-180 words. Cite a specific subsidy program or analysis, and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["subsidy", "ira", "program", "analysis", "study", "report", "tax credit"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "though"],
        },
    },
    {
        "name": "homework",
        "prompt": "Argue for or against the claim: 'Homework should be eliminated in primary schools (grades K-5).' 120-180 words. Cite a specific study or jurisdiction; address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["study", "research", "finland", "report", "meta-analysis", "kohn", "cooper"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
    {
        "name": "vegan_default",
        "prompt": "Argue for or against the claim: 'Public institutions should make vegan meals the default option in cafeterias.' 120-180 words. Cite a specific trial or study, and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["trial", "study", "experiment", "default", "nudge", "research", "pilot"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
    {
        "name": "city_density",
        "prompt": "Argue for or against the claim: 'Single-family zoning should be banned in cities with populations above 100,000.' 120-180 words. Cite a specific city's reform and address the strongest counter-argument.",
        "rubric": {
            "word_min": 120, "word_max": 220,
            "must_include_any": ["minneapolis", "oregon", "policy", "study", "report", "research", "zoning"],
            "counter_keywords": ["however", "but", "critics", "counter", "concede", "yet", "opponents"],
        },
    },
]


def get_topic(seed: int) -> dict:
    """Pick a topic deterministically from seed."""
    return TOPICS[seed % len(TOPICS)]


def score_against_rubric(text: str, rubric: dict) -> tuple[int, int, list[str]]:
    """Return (score, max_score, failed_checks).

    Checks:
      1. Word count in [word_min, word_max] (1 point)
      2. At least one keyword from must_include_any present (1 point)
      3. At least one counter-keyword present (1 point)
      4. Includes a specific year, percentage, or other concrete number (1 point)

    Total max = 4. Higher is better; error = max - actual.
    """
    failed: list[str] = []
    score = 0
    text_lower = (text or "").lower()
    n_words = len((text or "").split())
    if rubric["word_min"] <= n_words <= rubric["word_max"]:
        score += 1
    else:
        failed.append(f"word_count={n_words}")
    if any(k.lower() in text_lower for k in rubric["must_include_any"]):
        score += 1
    else:
        failed.append("no_cited_source")
    if any(k.lower() in text_lower for k in rubric["counter_keywords"]):
        score += 1
    else:
        failed.append("no_counter_argument")
    # Concrete number: 4-digit year, percentage, or any multi-digit numeric token
    import re
    if re.search(r"\b\d{2,}(?:\.\d+)?\s*%?\b", text or ""):
        score += 1
    else:
        failed.append("no_concrete_number")
    return score, 4, failed
