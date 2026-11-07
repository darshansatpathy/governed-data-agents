"""A deterministic planner for a bounded grammar of analytics questions.

It matches the semantic layer's names and synonyms, dimension values, time expressions and a
few ranking words. It is predictable and needs no model, and it says when a question is outside
what it understands instead of guessing. Open-ended phrasing is the LLM planner's job; both
produce the same QueryPlan and go through the same checks.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import ValidationError

from gda.access import Principal
from gda.plan import Filter, Order, QueryPlan, TimeRange
from gda.planner import PlanResult
from gda.semantic import Grain, SemanticModel

GROUP_CUES = {"by", "per", "across", "each", "every", "which", "what", "list", "split", "breakdown"}
CONNECTORS = {"and", ",", "&", "or", "the", "a", "an", "all", "of", "each", "every", "their", "its", "down"}
NEGATIONS = {"excluding", "except", "not", "without", "exclude", "excludes", "ex", "other"}
RANK_DESC = {"top", "highest", "most", "best", "largest", "biggest", "leading"}
RANK_ASC = {"bottom", "lowest", "least", "worst", "smallest", "fewest"}
SUPERLATIVES = (RANK_DESC | RANK_ASC) - {"top", "bottom"}
UNSUPPORTED = {
    "growth": "growth and period-over-period comparisons",
    "grew": "growth and period-over-period comparisons",
    "compare": "comparisons between periods or groups",
    "compared": "comparisons between periods or groups",
    "versus": "comparisons between periods or groups",
    "vs": "comparisons between periods or groups",
    "yoy": "growth and period-over-period comparisons",
    "forecast": "forecasts",
    "predict": "forecasts",
    "projected": "forecasts",
    "why": "explanations of causes",
}
STOPWORDS = {
    "what",
    "was",
    "were",
    "is",
    "are",
    "the",
    "a",
    "an",
    "of",
    "for",
    "in",
    "on",
    "to",
    "and",
    "or",
    "me",
    "show",
    "give",
    "tell",
    "how",
    "much",
    "many",
    "did",
    "do",
    "does",
    "we",
    "our",
    "us",
    "i",
    "my",
    "with",
    "from",
    "had",
    "have",
    "has",
    "total",
    "overall",
    "please",
    "list",
    "get",
    "find",
    "see",
    "which",
    "who",
    "by",
    "per",
    "across",
    "each",
    "every",
    "all",
    "their",
    "its",
    "that",
    "this",
    "these",
    "those",
    "it",
    "be",
    "been",
    "at",
    "as",
    "there",
    "same",
    "make",
    "made",
    "generate",
    "generated",
    "bring",
    "brought",
    "come",
    "came",
    "sold",
    "sell",
    "split",
    "down",
    "breakdown",
    "broken",
    "order",
    "ordered",
    "can",
    "you",
    "could",
    "would",
    "number",
    "value",
    "amount",
    "only",
    "just",
    "than",
    "then",
    "into",
    "time",
    "period",
    ",",
    "&",
    "between",
    "since",
    "year",
    "years",
    "month",
    "months",
    "quarter",
    "data",
}
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS |= {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
GRAIN_WORDS = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year", "annual": "year", "annually": "year"}


@dataclass
class Tok:
    kind: str  # word, metric, dim, value, grain, quote, time, num
    text: str
    ref: object = None


class RulePlanner:
    name = "rules"

    def __init__(self, model: SemanticModel, as_of: dt.date | None = None) -> None:
        self.model = model
        self.as_of = as_of or dt.date.today()
        phrases: dict[tuple[str, ...], tuple[str, object]] = {}
        for name, synonyms in model.synonyms().items():
            kind = "metric" if model.is_measure_or_metric(name) else "dim"
            for s in synonyms:
                phrases[tuple(s.split())] = (kind, name)
        for dname, dim in model.dimensions.items():
            for v in dim.values:
                phrases.setdefault(tuple(v.lower().split()), ("value", (dname, v)))
        for grain in model.time_dimension.grains:
            phrases.setdefault((grain,), ("grain", grain))
        for word, named in GRAIN_WORDS.items():
            phrases[(word,)] = ("grain", named)
        phrases[("over", "time")] = ("grain", "month")
        phrases[("trend",)] = ("grain", "month")
        self.phrases = phrases
        self.longest = max(len(p) for p in phrases)

    # ---- entry point ----------------------------------------------------------------------

    def plan(self, question: str, principal: Principal | None = None) -> PlanResult:
        result = PlanResult(self.name)
        quoted: list[str] = []

        def keep_quote(m: re.Match[str]) -> str:
            quoted.append(next(g for g in m.groups() if g is not None))
            return f" __q{len(quoted) - 1}__ "

        text = re.sub(r'"([^"]{1,200})"|“([^”]{1,200})”', keep_quote, question)
        low = text.lower()
        low = re.sub(r"(?<=\w)'s\b", "", low)
        low = re.sub(r"[?!;:()\[\]{}]", " ", low)
        low = re.sub(r"\.(?=\s|$)", " ", low).replace(",", " , ")
        low = " " + re.sub(r"\s+", " ", low).strip() + " "

        for word in re.findall(r"[a-z]+", low):
            if word in UNSUPPORTED:
                result.unsupported = f"{UNSUPPORTED[word]} are not supported by the rule planner"
                return result
        ranges: list[TimeRange] = []
        try:
            low = self._times(low, ranges)
        except ValueError as exc:  # includes pydantic's ValidationError
            result.unsupported = "the dates do not form a valid period" + (": end before start" if "after start" in str(exc) else "")
            return result
        if len(ranges) > 1:
            result.unsupported = "more than one time period: comparisons between periods are not supported by the rule planner"
            return result

        tokens = self._tokens(low, quoted, ranges)
        return self._interpret(tokens, ranges[0] if ranges else None, result)

    # ---- time expressions -----------------------------------------------------------------

    def _times(self, low: str, out: list[TimeRange]) -> str:
        today = self.as_of
        year_start = dt.date(today.year, 1, 1)
        q = (today.month - 1) // 3
        q_start = dt.date(today.year, 3 * q + 1, 1)

        def add(start: dt.date, end: dt.date) -> str:
            out.append(TimeRange(start=start, end=end))
            return f" __t{len(out) - 1}__ "

        def month_end(y: int, m: int) -> dt.date:
            return dt.date(y + (m == 12), m % 12 + 1, 1)

        prefix = r"(?:(?:in|during|for|over|throughout|within|of) )?(?:the )?"
        patterns: list[tuple[str, Callable[[re.Match[str]], str]]] = [
            (
                r"between (\d{4}-\d{2}-\d{2}) and (\d{4}-\d{2}-\d{2})",
                lambda m: add(dt.date.fromisoformat(m[1]), dt.date.fromisoformat(m[2]) + dt.timedelta(days=1)),
            ),
            (r"(?:since|from) (\d{4}-\d{2}-\d{2})", lambda m: add(dt.date.fromisoformat(m[1]), today + dt.timedelta(days=1))),
            (
                prefix + r"(first|second) half of (20\d\d)",
                lambda m: add(
                    dt.date(int(m[2]), 1 if m[1] == "first" else 7, 1), dt.date(int(m[2]), 7, 1) if m[1] == "first" else dt.date(int(m[2]) + 1, 1, 1)
                ),
            ),
            (
                prefix + r"h([12]) (20\d\d)",
                lambda m: add(
                    dt.date(int(m[2]), 1 if m[1] == "1" else 7, 1), dt.date(int(m[2]), 7, 1) if m[1] == "1" else dt.date(int(m[2]) + 1, 1, 1)
                ),
            ),
            (prefix + r"q([1-4]) (?:of )?(20\d\d)", lambda m: add(dt.date(int(m[2]), 3 * int(m[1]) - 2, 1), month_end(int(m[2]), 3 * int(m[1])))),
            (
                prefix + r"(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r") (20\d\d)",
                lambda m: add(dt.date(int(m[2]), MONTHS[m[1]], 1), month_end(int(m[2]), MONTHS[m[1]])),
            ),
            (prefix + r"(?:year )?(20\d\d)", lambda m: add(dt.date(int(m[1]), 1, 1), dt.date(int(m[1]) + 1, 1, 1))),
            (prefix + r"(?:last|previous|prior) year", lambda m: add(dt.date(today.year - 1, 1, 1), year_start)),
            (prefix + r"(?:this year|year to date|ytd)", lambda m: add(year_start, today + dt.timedelta(days=1))),
            (prefix + r"(?:last|previous|prior) quarter", lambda m: add(dt.date(q_start.year - (q == 0), (3 * q - 3) % 12 + 1, 1), q_start)),
            (prefix + r"(?:this quarter|quarter to date|qtd)", lambda m: add(q_start, today + dt.timedelta(days=1))),
            (
                prefix + r"(?:last|previous|prior) month",
                lambda m: add(dt.date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1), today.replace(day=1)),
            ),
            (prefix + r"(?:this month|month to date|mtd)", lambda m: add(today.replace(day=1), today + dt.timedelta(days=1))),
            (
                prefix + r"(?:last|past|previous) (\d{1,3}) days",
                lambda m: add(today - dt.timedelta(days=int(m[1]) - 1), today + dt.timedelta(days=1)),
            ),
        ]
        for pattern, handler in patterns:
            low = re.sub(r"(?<= )" + pattern + r"(?= )", handler, low)
        return low

    # ---- tokens ------------------------------------------------------------------------------

    def _tokens(self, low: str, quoted: list[str], ranges: list[TimeRange]) -> list[Tok]:
        words = low.split()
        tokens: list[Tok] = []
        i = 0
        while i < len(words):
            w = words[i]
            if m := re.fullmatch(r"__q(\d+)__", w):
                tokens.append(Tok("quote", quoted[int(m[1])]))
                i += 1
                continue
            if m := re.fullmatch(r"__t(\d+)__", w):
                tokens.append(Tok("time", w, ranges[int(m[1])]))
                i += 1
                continue
            for n in range(min(self.longest, len(words) - i), 0, -1):
                key = tuple(words[i : i + n])
                if key in self.phrases:
                    kind, ref = self.phrases[key]
                    tokens.append(Tok(kind, " ".join(key), ref))
                    i += n
                    break
            else:
                tokens.append(Tok("num" if w.isdigit() else "word", w))
                i += 1
        return tokens

    # ---- interpretation -----------------------------------------------------------------

    def _interpret(self, tokens: list[Tok], time_range: TimeRange | None, result: PlanResult) -> PlanResult:
        words = [t.text for t in tokens if t.kind == "word"]
        ranking = next((w for w in words if w in RANK_DESC | RANK_ASC), None)
        listing = bool(tokens) and tokens[0].text in {"list", "which", "what"} and len(tokens) > 1 and tokens[1].kind == "dim"
        metrics: list[str] = []
        dims: list[str] = []
        grain: str | None = None
        filters: dict[str, tuple[str, list[str]]] = {}
        order_metric: str | None = None
        limit: int | None = None
        ignored: list[str] = []
        grouping = False
        negate = False
        prev: Tok | None = None

        for idx, tok in enumerate(tokens):
            nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
            if tok.kind == "word":
                w = tok.text
                after_cue = grouping and prev is not None and prev.text in {"by", "per", "across"}
                if w in GROUP_CUES or w in RANK_DESC | RANK_ASC:
                    grouping = True
                elif w in NEGATIONS:
                    negate = True
                elif w not in CONNECTORS:
                    grouping = False
                    if w not in {"in", "for", "from", "on", "to", "than", "with"}:
                        negate = False
                if w not in STOPWORDS and w not in NEGATIONS and w not in RANK_DESC | RANK_ASC and w not in CONNECTORS:
                    if after_cue:
                        result.unsupported = f"no dimension called '{w}' in the semantic layer"
                        return result
                    ignored.append(w)
            elif tok.kind == "num":
                if prev is not None and prev.text in {"top", "bottom"}:
                    limit = int(tok.text)
                else:
                    ignored.append(tok.text)
            elif tok.kind == "metric":
                name = str(tok.ref)
                if name not in metrics:
                    metrics.append(name)
                if ranking and prev is not None and prev.text == "by" and dims:
                    order_metric = name
                grouping = False
            elif tok.kind == "dim":
                name = str(tok.ref)
                if nxt is not None and nxt.kind == "quote":
                    prev = tok
                    continue  # a filter on this dimension; handled by the quote
                context_noun = prev is not None and prev.kind == "value"
                if not context_noun and (grouping or (ranking and ranking in SUPERLATIVES | {"top", "bottom"}) or listing):
                    if name not in dims:
                        dims.append(name)
                else:
                    result.notes.append(f"'{tok.text}' read as context, not as a breakdown")
            elif tok.kind == "value":
                dname, value = cast(tuple[str, str], tok.ref)
                op, values = filters.get(dname, ("not_in" if negate else "in", []))
                if value not in values:
                    values.append(value)
                filters[dname] = (op, values)
            elif tok.kind == "grain":
                if grouping or tok.text in GRAIN_WORDS or tok.text in {"over time", "trend"}:
                    grain = str(tok.ref)
                else:
                    result.notes.append(f"'{tok.text}' read as context, not as a breakdown")
            elif tok.kind == "quote":
                if prev is not None and prev.kind == "dim":
                    dname = str(prev.ref)
                    op, values = filters.get(dname, ("not_in" if negate else "in", []))
                    filters[dname] = (op, [*values, tok.text])
                else:
                    result.unsupported = f'the quoted text "{tok.text}" does not follow a field name, so it cannot be used as a filter'
                    return result
            prev = tok

        if not metrics and not (listing and dims):
            available = ", ".join(sorted([*self.model.measures, *self.model.metrics]))
            result.unsupported = f"no measure in the question matches the semantic layer (available: {available})"
            return result
        if ignored:
            result.notes.append("not used: " + ", ".join(dict.fromkeys(ignored)))

        order = None
        if ranking and metrics:
            direction: Literal["asc", "desc"] = "asc" if ranking in RANK_ASC else "desc"
            order = Order(by=order_metric or metrics[0], direction=direction)
            if limit is None:
                limit = 10 if ranking in {"top", "bottom"} else 1
        elif ranking and not metrics:
            result.unsupported = f"'{ranking}' needs a measure to rank by"
            return result
        metrics = [self._consistent_measure(m, filters, result) for m in metrics]
        try:
            result.plan = QueryPlan(
                metrics=metrics,
                dimensions=dims,
                grain=cast(Grain | None, grain),
                filters=[
                    Filter(field=d, op="not_in" if op == "not_in" else ("eq" if len(v) == 1 else "in"), values=v) for d, (op, v) in filters.items()
                ],
                time_range=time_range,
                order=order,
                limit=limit,
            )
        except ValidationError as exc:
            result.unsupported = "the question could not be turned into a valid plan: " + exc.errors()[0]["msg"]
        return result

    def _consistent_measure(self, name: str, filters: dict[str, tuple[str, list[str]]], result: PlanResult) -> str:
        """'orders that were cancelled': use the measure defined for those rows, not one that excludes them."""
        measure = self.model.measures.get(name)
        if measure is None:
            return name
        for cond in measure.where:
            op, values = filters.get(cond.field, ("", []))
            if op == "in" and cond.op == "not_in" and set(values) <= set(cond.values):
                for other, m in self.model.measures.items():
                    if (m.agg, m.column) == (measure.agg, measure.column) and any(
                        c.field == cond.field and c.op == "in" and set(values) <= set(c.values) for c in m.where
                    ):
                        del filters[cond.field]
                        result.notes.append(f"{name} excludes {', '.join(values)} by definition; used {other}")
                        return other
        return name


def plan_question(model: SemanticModel, question: str, principal: Principal | None = None, as_of: dt.date | None = None) -> PlanResult:
    return RulePlanner(model, as_of).plan(question, principal)
