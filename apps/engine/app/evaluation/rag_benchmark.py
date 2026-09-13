from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Callable, Iterable


@dataclass(frozen=True, slots=True)
class BenchmarkDocument:
    filename: str
    content: str


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    category: str
    question: str
    expected_files: tuple[str, ...]
    required_tokens: tuple[str, ...]
    answerable: bool = True


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    total_cases: int
    answerable_cases: int
    no_evidence_cases: int
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    answer_accuracy: float
    evidence_accuracy: float
    citation_accuracy: float
    no_evidence_accuracy: float
    hallucination_rate: float
    category_counts: dict[str, int]
    category_recall_at_5: dict[str, float]

    def as_dict(self) -> dict:
        return asdict(self)


def build_engineering_benchmark_v1() -> tuple[list[BenchmarkDocument], list[BenchmarkCase]]:
    """Return a public, synthetic 100-question engineering retrieval benchmark.

    The fixture intentionally contains no production/customer data. It mirrors the
    structure of municipal-engineering records: quantities, pressures, dates,
    responsible roles, handover states, revision markers and asset identifiers.
    """

    documents: list[BenchmarkDocument] = []
    cases: list[BenchmarkCase] = []

    for index in range(1, 19):
        sector = f"S{index:02d}"
        filename = f"{sector}-engineering-record.md"
        quantity = 1200 + index * 37
        pressure = 0.60 + index * 0.01
        month = ((index - 1) % 9) + 1
        day = ((index * 2 - 1) % 27) + 1
        date = f"2026-{month:02d}-{day:02d}"
        role = f"Engineer-{chr(64 + index)}"
        status = ("approved", "pending", "completed")[index % 3]
        revision_current = f"REV-{index:02d}-CURRENT"
        revision_old = f"REV-{index:02d}-OLD"
        chamber = f"VC-{index:02d}"

        documents.append(
            BenchmarkDocument(
                filename=filename,
                content=(
                    f"# Sector {sector} Engineering Record\n"
                    f"Sector {sector} secondary pipeline verified quantity is {quantity} m. "
                    f"The hydrostatic test pressure is {pressure:.2f} MPa. "
                    f"The latest acceptance coordination date is {date}. "
                    f"The responsible role is held by {role}. "
                    f"The current handover status is {status}. "
                    f"Revision marker {revision_current} supersedes {revision_old}. "
                    f"Valve chamber {chamber} is associated with Sector {sector}."
                ),
            )
        )

        facts = (
            (
                "quantity",
                f"What is the verified secondary pipeline quantity for Sector {sector}?",
                (str(quantity), "m"),
            ),
            (
                "pressure",
                f"What hydrostatic test pressure is recorded for Sector {sector}?",
                (f"{pressure:.2f}", "MPa"),
            ),
            (
                "date",
                f"What is the latest acceptance coordination date for Sector {sector}?",
                (date,),
            ),
            (
                "role",
                f"Who holds the responsible role for Sector {sector}?",
                (role,),
            ),
            (
                "status",
                f"What is the current handover status of Sector {sector}?",
                (status,),
            ),
        )
        for category, question, required_tokens in facts:
            cases.append(
                BenchmarkCase(
                    id=f"{sector.lower()}-{category}",
                    category=category,
                    question=question,
                    expected_files=(filename,),
                    required_tokens=required_tokens,
                )
            )

    for index in range(1, 11):
        cases.append(
            BenchmarkCase(
                id=f"negative-{index:02d}",
                category="no_evidence",
                question=f"What is the certified value for nonexistent asset ZX-{900 + index}?",
                expected_files=(),
                required_tokens=(),
                answerable=False,
            )
        )

    if len(cases) != 100:
        raise AssertionError(f"Engineering benchmark must contain exactly 100 cases, got {len(cases)}")
    return documents, cases


def evaluate_retrieval(
    cases: Iterable[BenchmarkCase],
    search: Callable[[str, int], list[dict]],
) -> BenchmarkReport:
    case_list = list(cases)
    answerable = [case for case in case_list if case.answerable]
    no_evidence = [case for case in case_list if not case.answerable]

    hits_at_1 = 0
    hits_at_5 = 0
    hits_at_10 = 0
    reciprocal_rank_total = 0.0
    answer_hits = 0
    evidence_hits = 0
    citation_hits = 0
    no_evidence_hits = 0
    category_counts = Counter(case.category for case in case_list)
    category_answerable = Counter(case.category for case in answerable)
    category_hits_at_5: defaultdict[str, int] = defaultdict(int)

    for case in case_list:
        results = search(case.question, 10)

        if not case.answerable:
            # Semantic retrieval may legitimately return weak nearest neighbours.
            # A no-evidence query is correctly unsupported when no returned
            # candidate has lexical documentary evidence. The complement is the
            # deterministic hallucination/support-false-positive rate.
            if all(hit.get("lexical_rank") is None for hit in results):
                no_evidence_hits += 1
            continue

        relevant_rank: int | None = None
        relevant_hit: dict | None = None
        for rank, hit in enumerate(results, start=1):
            if str(hit.get("filename") or "") in case.expected_files:
                relevant_rank = rank
                relevant_hit = hit
                break

        if relevant_rank is None:
            continue
        if relevant_rank <= 1:
            hits_at_1 += 1
        if relevant_rank <= 5:
            hits_at_5 += 1
            category_hits_at_5[case.category] += 1
        if relevant_rank <= 10:
            hits_at_10 += 1
        reciprocal_rank_total += 1.0 / relevant_rank

        if relevant_hit is not None:
            evidence = str(relevant_hit.get("content") or "")
            tokens_present = all(token.lower() in evidence.lower() for token in case.required_tokens)
            if tokens_present:
                evidence_hits += 1
                # The public benchmark uses extractive ground truth: an answer is
                # correct only when the authoritative retrieved evidence contains
                # every required answer token.
                answer_hits += 1
            citation = str(relevant_hit.get("citation_label") or "")
            if any(citation.startswith(filename) for filename in case.expected_files):
                citation_hits += 1

    denominator = len(answerable) or 1
    no_evidence_denominator = len(no_evidence) or 1
    no_evidence_accuracy = no_evidence_hits / no_evidence_denominator
    category_recall = {
        category: round(category_hits_at_5[category] / count, 6)
        for category, count in sorted(category_answerable.items())
    }
    return BenchmarkReport(
        total_cases=len(case_list),
        answerable_cases=len(answerable),
        no_evidence_cases=len(no_evidence),
        recall_at_1=round(hits_at_1 / denominator, 6),
        recall_at_5=round(hits_at_5 / denominator, 6),
        recall_at_10=round(hits_at_10 / denominator, 6),
        mrr=round(reciprocal_rank_total / denominator, 6),
        answer_accuracy=round(answer_hits / denominator, 6),
        evidence_accuracy=round(evidence_hits / denominator, 6),
        citation_accuracy=round(citation_hits / denominator, 6),
        no_evidence_accuracy=round(no_evidence_accuracy, 6),
        hallucination_rate=round(1.0 - no_evidence_accuracy, 6),
        category_counts=dict(sorted(category_counts.items())),
        category_recall_at_5=category_recall,
    )
