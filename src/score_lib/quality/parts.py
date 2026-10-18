"""A quality score's parts: each a named measure turned into 0 to 100 on a ramp with three marks (100 at its good
mark, 50 at its line, 0 at its bad mark), with what was measured and how. 50 is a gate's own line wherever the
framework has a gate for the measure: under 50 is what that gate fails. A part that could not be measured, or that
needs a paid judge, is listed with no value and left out of the score."""

NOT_MEASURED = "not measured"
NEEDS_PAID_JUDGE = "needs a paid judge"
NOT_JUDGED = "not judged"


def ramp(measured, good, line, bad):
    """A measure on 0..100: 100 at or past `good`, 50 at `line`, 0 at or past `bad`, straight between (the marks may
    run up or down)."""
    if (good - bad) == 0:
        raise ValueError("a ramp needs its good and bad marks apart")
    rising = good > bad
    if (measured >= good) if rising else (measured <= good):
        return 100
    if (measured <= bad) if rising else (measured >= bad):
        return 0
    if (measured >= line) if rising else (measured <= line):
        return round(50 + 50 * (measured - line) / (good - line))
    return round(50 * (measured - bad) / (line - bad))


def scored(name, value, measured, how, weight=1.0, unit="", picture=None):
    """A measured part."""
    found = {"name": name, "value": int(value), "weight": weight, "measured": measured, "unit": unit, "how": how}
    if picture:
        found["picture"] = picture
    return found


def ramped(name, measured, marks, how, weight=1.0, unit="", picture=None):
    """A measured part valued on a ramp: marks (good, line, bad); `how` gains the marks."""
    good, line, bad = marks
    text = f"{how}; 100 at {good}, 50 at {line}, 0 at {bad}{' ' + unit if unit else ''}"
    return scored(name, ramp(measured, good, line, bad), measured, text, weight, unit, picture)


def unmeasured(name, why, status=NOT_MEASURED):
    """A part with no value: not measured (with why), not judged for this entry, or needing a paid judge."""
    return {"name": name, "value": None, "status": status, "how": why}


def total(parts):
    """The weighted mean of the measured parts, rounded, or None when none was measured."""
    valued = [part for part in parts if part.get("value") is not None]
    weight = sum(part["weight"] for part in valued)
    if not valued or weight <= 0:
        return None
    return round(sum(part["value"] * part["weight"] for part in valued) / weight)


def gates(name, verdicts, where, weight=1.0):
    """A part from a stage's gate answers: the share of its gates that pass (fail and unknown block alike), each
    gate's answer and reason named."""
    if not verdicts:
        return unmeasured(name, f"no judgement of {where} in the store")
    answers = {gate: verdict.get("answer") for gate, verdict in verdicts.items()}
    passing = sum(answer == "pass" for answer in answers.values())
    listed = "; ".join(f"{gate} {answer}: {verdicts[gate].get('reason', '')[:120]}" for gate, answer in
                       sorted(answers.items()))
    return scored(name, round(100 * passing / len(answers)), f"{passing} of {len(answers)} pass",
                  f"share of {where}'s gates that pass (fail and unknown block alike): {listed}", weight)


def held(name, scores, weight=1.0, what="entries"):
    """A part from the quality scores of the entries an entry holds: their mean, with the weakest named."""
    known = {address: value for address, value in scores.items() if value is not None}
    if not known:
        return unmeasured(name, f"none of its {len(scores)} {what} has a quality score")
    weakest = sorted(known.items(), key=lambda item: item[1])[:3]
    mean = sum(known.values()) / len(known)
    under = sum(value < 50 for value in known.values())
    return scored(name, round(mean), f"mean of {len(known)}", f"mean quality score of its {len(known)} {what} "
                  f"({under} under 50; weakest {', '.join(f'{address} {value}' for address, value in weakest)})",
                  weight)
