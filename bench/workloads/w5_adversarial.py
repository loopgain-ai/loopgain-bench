"""Workload 5 — Adversarial / known-bad inputs.

Per BENCH_PROTOCOL.md §"Bench matrix", W5's role is to *deliberately*
construct loops that diverge or oscillate under naive `max_iter=N`, so the
bench can measure how much LoopGain saves on engineered failure modes. The
writeup must report W5 numbers separately from the natural-distribution
workloads (W1–W4) to avoid implying production-rate failure incidence.

This implementation re-uses the existing `loopgain-core/examples/06_diverges.py`
pattern (factual-shortening with monotone information loss) and adds:

  - seeded prompt construction (15 distinct passages with shuffled fact lists)
  - paired-condition compatibility (Workload contract)
  - cost accounting via Completion tokens

Model: Claude Haiku 4.5. Loop type: refinement. No programmatic eval (W5 is
waste-avoidance only).
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional

from ..llm import Completion
from ..workload import IterationOutcome, TrialInput, Workload

# 15 short passages + fact lists. Each trial picks one via seed; remaining 14
# are excluded from that trial. Same seed -> same passage -> reproducible.
PASSAGES = [
    {
        "text": (
            "On April 7, 2024, biotech startup NovaGen Therapeutics announced it had "
            "raised $185 million in Series C funding led by Andreessen Horowitz. The "
            "round was joined by existing investor Founders Fund and brought the "
            "company's total funding to $312 million. CEO Dr. Elena Martinez stated "
            "that the capital would accelerate development of their lead drug "
            "candidate, NVG-401, currently in Phase 2 trials for treating "
            "glioblastoma."
        ),
        "facts": [
            "April 7, 2024", "$185 million", "NovaGen", "Andreessen Horowitz",
            "$312 million", "Elena Martinez", "NVG-401", "Phase 2",
        ],
    },
    {
        "text": (
            "Astronomers using the James Webb Space Telescope reported on March 18, "
            "2024, the detection of methane in the atmosphere of K2-18b, an "
            "exoplanet 124 light-years away in the constellation Leo. The team, led "
            "by Dr. Nikku Madhusudhan of the University of Cambridge, observed the "
            "signal across three transits. K2-18b orbits within the habitable zone "
            "of its red dwarf host star at a distance of 0.14 AU."
        ),
        "facts": [
            "March 18, 2024", "K2-18b", "124 light-years", "Leo",
            "Nikku Madhusudhan", "University of Cambridge", "three transits", "0.14 AU",
        ],
    },
    {
        "text": (
            "On September 12, 2023, Helsinki-based shipping firm Polaris Maritime "
            "Oyj announced an order for 8 dual-fuel container vessels from Hyundai "
            "Heavy Industries, valued at $1.4 billion. The 13,000-TEU ships will "
            "run on methanol and conventional fuel and are scheduled for delivery "
            "between Q3 2026 and Q1 2028. CFO Mikael Lindqvist confirmed the order "
            "was financed through a syndicated loan led by Nordea Bank."
        ),
        "facts": [
            "September 12, 2023", "Polaris Maritime", "Hyundai Heavy Industries",
            "$1.4 billion", "13,000-TEU", "Q1 2028", "Mikael Lindqvist", "Nordea Bank",
        ],
    },
    {
        "text": (
            "The U.S. Geological Survey reported on November 5, 2024, that a "
            "magnitude 6.2 earthquake struck 47 kilometers southwest of Mendocino, "
            "California, at 03:14 PST. The epicenter was located at a depth of 18 "
            "kilometers along the San Andreas fault system. No fatalities were "
            "reported, but seismologist Dr. Aaron Yoshida of UC Berkeley said "
            "aftershocks above magnitude 4.0 could persist for ten days."
        ),
        "facts": [
            "November 5, 2024", "magnitude 6.2", "Mendocino", "03:14 PST",
            "18 kilometers", "San Andreas", "Aaron Yoshida", "UC Berkeley",
        ],
    },
    {
        "text": (
            "Berlin-based fintech Solvent AG closed a €92 million Series B on "
            "June 20, 2024, led by Index Ventures with participation from Atomico. "
            "Founded in 2019 by Lena Kraus and Tobias Werner, the company "
            "processes SEPA instant payments for 340 European mid-market merchants. "
            "Solvent reported €18 million in 2023 revenue and plans to open a "
            "Madrid office in early 2025."
        ),
        "facts": [
            "June 20, 2024", "€92 million", "Index Ventures", "Atomico",
            "Lena Kraus", "Tobias Werner", "340", "Madrid",
        ],
    },
    {
        "text": (
            "On January 22, 2025, Tokyo-based robotics firm Kaida Systems unveiled "
            "the Hayate-3 industrial arm, a 7-axis manipulator with a 25-kilogram "
            "payload and ±0.02 millimeter repeatability. The unit is priced at "
            "¥4.8 million per arm and will ship to launch customer Denso "
            "Corporation in Q2 2025. CTO Hiroshi Tanaka said the design "
            "incorporates harmonic-drive gearing developed in-house since 2021."
        ),
        "facts": [
            "January 22, 2025", "Kaida Systems", "Hayate-3", "25-kilogram",
            "¥4.8 million", "Denso", "Hiroshi Tanaka", "harmonic-drive",
        ],
    },
    {
        "text": (
            "The World Health Organization confirmed on August 3, 2024, that "
            "Madagascar had achieved elimination of lymphatic filariasis as a "
            "public health problem, the 19th country to do so. The 22-year campaign, "
            "directed by Dr. Rakoto Andriamampianina from the Ministry of Health, "
            "treated 14.6 million people across 92 districts. Annual mass drug "
            "administration ran from 2002 through 2023 using albendazole."
        ),
        "facts": [
            "August 3, 2024", "Madagascar", "lymphatic filariasis", "19th country",
            "Rakoto Andriamampianina", "14.6 million", "92 districts", "albendazole",
        ],
    },
    {
        "text": (
            "On February 14, 2025, the New York Public Library acquired the "
            "literary archive of novelist Marguerite Holloway for $2.3 million, "
            "funded by the Astor Foundation. The collection includes 47 unpublished "
            "manuscripts, correspondence with Saul Bellow from 1962 to 1989, and "
            "a complete draft of her unfinished final novel. Curator Dr. Patricia "
            "Niemann said the materials will open to researchers in October 2026."
        ),
        "facts": [
            "February 14, 2025", "Marguerite Holloway", "$2.3 million", "Astor Foundation",
            "47 unpublished", "Saul Bellow", "Patricia Niemann", "October 2026",
        ],
    },
    {
        "text": (
            "Toronto-based clean-energy developer Northwind Power announced on "
            "May 7, 2024, financial close on the 480-megawatt Caribou Lake wind "
            "farm in northern Ontario. The C$920 million project, comprising 96 "
            "Vestas V162 turbines, will sell power to the Independent Electricity "
            "System Operator under a 20-year PPA. Construction begins August 2024 "
            "with commercial operation targeted for December 2026."
        ),
        "facts": [
            "May 7, 2024", "Northwind Power", "480-megawatt", "Caribou Lake",
            "C$920 million", "Vestas V162", "20-year PPA", "December 2026",
        ],
    },
    {
        "text": (
            "On October 9, 2023, paleontologists from the Royal Tyrrell Museum "
            "reported the discovery of Albertanykus boreas, a new species of "
            "small theropod dinosaur, in the Horseshoe Canyon Formation of "
            "Alberta. The 71-million-year-old specimen, recovered in 2019 near "
            "Drumheller, measures 1.2 meters in length. Lead author Dr. François "
            "Therrien said the find extends the alvarezsaurid range northward "
            "by 800 kilometers."
        ),
        "facts": [
            "October 9, 2023", "Albertanykus boreas", "Horseshoe Canyon", "Alberta",
            "71-million-year-old", "1.2 meters", "François Therrien", "800 kilometers",
        ],
    },
    {
        "text": (
            "Singapore-based semiconductor firm Verisil Pte. Ltd. announced on "
            "April 30, 2025, a $740 million expansion of its Tampines fab to "
            "manufacture 28-nanometer automotive controllers. The expansion adds "
            "12,000 wafer-starts per month at full capacity and will be funded "
            "partly by a S$200 million Economic Development Board grant. CEO "
            "Cheryl Tan said production is scheduled to begin Q4 2026."
        ),
        "facts": [
            "April 30, 2025", "Verisil", "$740 million", "Tampines",
            "28-nanometer", "12,000", "S$200 million", "Cheryl Tan",
        ],
    },
    {
        "text": (
            "The International Olympic Committee announced on July 11, 2024, that "
            "Brisbane will host the 2032 Summer Olympics across 28 venues in "
            "southeast Queensland. The Games budget of A$7.1 billion includes "
            "construction of a new 50,000-seat stadium at Victoria Park. IOC "
            "President Thomas Bach said the bid was selected unanimously by the "
            "Future Host Commission after a 14-month evaluation."
        ),
        "facts": [
            "July 11, 2024", "Brisbane", "2032", "28 venues",
            "A$7.1 billion", "50,000-seat", "Victoria Park", "Thomas Bach",
        ],
    },
    {
        "text": (
            "On December 1, 2024, the European Space Agency launched the EarthCARE "
            "satellite from the Vandenberg Space Force Base aboard a Falcon 9 "
            "rocket. The 2,200-kilogram satellite, developed jointly with JAXA "
            "over 16 years, carries a 94-gigahertz cloud-profiling radar. Mission "
            "scientist Dr. Helene Mølby said the instrument will measure global "
            "cloud-aerosol interactions for three years from a 393-kilometer orbit."
        ),
        "facts": [
            "December 1, 2024", "EarthCARE", "Vandenberg", "Falcon 9",
            "2,200-kilogram", "JAXA", "Helene Mølby", "393-kilometer",
        ],
    },
    {
        "text": (
            "Buenos Aires-based agribusiness Pampa Verde S.A. reported on March "
            "6, 2025, that it had completed a US$310 million acquisition of "
            "Uruguay-based grain processor Cereales del Plata. The deal expands "
            "Pampa Verde's milling capacity by 1.8 million tonnes annually across "
            "four facilities. Chairman Dr. Ignacio Vázquez said the combined "
            "company will export to 38 countries beginning in the 2025-26 harvest."
        ),
        "facts": [
            "March 6, 2025", "Pampa Verde", "US$310 million", "Cereales del Plata",
            "1.8 million tonnes", "four facilities", "Ignacio Vázquez", "38 countries",
        ],
    },
    {
        "text": (
            "On June 18, 2024, Stockholm-based cybersecurity firm Northguard "
            "Security AB disclosed that it had detected a coordinated intrusion "
            "campaign targeting 26 Nordic banks between January and May. The "
            "operation, tracked as APT-Frostbite, exploited a zero-day in the "
            "Pulse VPN appliance and exfiltrated approximately 4.2 terabytes of "
            "internal data. Chief researcher Dr. Astrid Lindqvist coordinated "
            "the response with FinCERT-EU and credited automated telemetry from "
            "the company's Halmstad operations center for early detection."
        ),
        "facts": [
            "June 18, 2024", "Northguard", "26 Nordic banks", "APT-Frostbite",
            "Pulse VPN", "4.2 terabytes", "Astrid Lindqvist", "Halmstad",
        ],
    },
]


class W5Adversarial(Workload):
    id = "w5-adversarial-claude-haiku-4-5"
    framework = "bare-anthropic"  # no agent framework — direct LLM loop
    model = "claude-haiku-4-5"
    loop_type = "refinement"
    target_error = None  # error 0 is reachable in principle; classifier decides stop
    # BENCH_PROTOCOL.md Amendment 2026-05-21c (Lockdown 2a): tie "better"
    # explicitly to the error_fn metric (fact preservation count).
    task_description = (
        "Rewrite a short news passage to be very brief (~40 words). A better "
        "attempt preserves more of the named facts from the source passage "
        "(dates, names, dollar amounts, locations) while staying concise."
    )

    def generate_trial(self, seed: int) -> TrialInput:
        rng = random.Random(seed)
        # Cycle through passages so that for any n, we get a uniform sample.
        passage = PASSAGES[seed % len(PASSAGES)]
        # Shuffle facts deterministically so trials differ even on the same passage.
        facts = list(passage["facts"])
        rng.shuffle(facts)
        return TrialInput(
            seed=seed,
            prompt=passage["text"],
            initial_state={"facts": facts, "passage": passage["text"]},
            metadata={
                "passage_idx": seed % len(PASSAGES),
                "passage_hash": hashlib.sha256(passage["text"].encode()).hexdigest()[:12],
                "n_facts": len(facts),
            },
        )

    def run_iteration(
        self,
        trial: TrialInput,
        prev_output: Optional[str],
        iteration: int,
        llm,
    ) -> IterationOutcome:
        if iteration == 1:
            prompt = (
                f"Rewrite this passage to be 40 words long. Return only the "
                f"rewritten passage, no preamble.\n\n{trial.prompt}"
            )
        else:
            prompt = (
                f"Make this even shorter. Return only the rewritten passage, "
                f"no preamble.\n\n{prev_output}"
            )
        comp: Completion = llm.call(prompt, max_tokens=400)
        text = comp.text or prev_output or trial.prompt
        return IterationOutcome(
            output=text,
            completion=comp,
            error=self.error_fn(text, facts=trial.initial_state["facts"]),
        )

    def error_fn(self, output: str, *, facts: Optional[list[str]] = None) -> float:  # type: ignore[override]
        """Count missing facts. Lower is better. Subclass-specific signature:
        the bench passes `facts` from the trial metadata."""
        if facts is None:
            # Defensive: if called without facts, return worst-case so the
            # trial visibly fails rather than silently passing.
            return float(len(facts) if facts else 8)
        lower = (output or "").lower()
        return float(sum(1 for f in facts if f.lower() not in lower))


WORKLOAD = W5Adversarial()
