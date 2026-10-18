"""A sound's quality score, judged on every file it plays (the worst file sets each part):

    prompt match  the CLAP score of a MOSS take against its prompt, kept in its record (CLAP_FLOOR 0.30 is the line);
                  an owner's recording has no prompt and is not judged
    loudness      integrated loudness against its category's target (tools/sound/loudness: ffmpeg's ebur128); the
                  kept bounds (OVER_LU over, UNDER_LU under) are the line
    true peak     against the -3 dBTP ceiling
    clipping      share of samples in runs of three or more at full scale
    length        the sound stage's own length rule against its brief (gates.length_fault)
    loop join     for a loop: the step across its join against the file's own largest steps
"""
import functools
import subprocess

import numpy as np

from score_lib.quality import files, parts, spots

CLAP = (0.55, 0.30, 0.15)
OVER_TARGET = (0.0, 1.0, 4.0)
UNDER_TARGET = (1.0, 6.0, 12.0)
TRUE_PEAK = (-4.0, -3.0, 0.0)
CLIPPED = (0.0, 0.0001, 0.001)
LOOP_JOIN = (1.0, 2.0, 4.0)
FULL_SCALE = 0.999
RATE = 48000


@functools.cache
def loudness_tool(context):
    context.repo_tool("tools/sound/loudness")
    import loudness
    return loudness


@functools.cache
def sound_gates(context):
    context.repo_tool(".")
    from score.stages.sound import gates
    return gates


def samples_of(path, context):
    """A file's samples, channels mixed, as float32 at RATE (ffmpeg)."""
    raw = subprocess.run([loudness_tool(context).ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
                          "-f", "f32le", "-ac", "1", "-ar", str(RATE), "-"], capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(raw, dtype=np.float32)


def clipped_runs(samples):
    """A mask of the samples in runs of three or more at full scale."""
    full = np.abs(samples) >= FULL_SCALE
    if not full.any():
        return full
    edges = np.diff(np.concatenate([[0], full.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    found = np.zeros_like(full)
    for start, end in zip(starts, ends):
        if end - start >= 3:
            found[start:end] = True
    return found


def measured_file(path, sound, file_count, context):
    """One file's measures: loudness, true peak, seconds, clipped share, length fault, loop join step."""
    tool = loudness_tool(context)
    level = tool.measure(path)
    samples = samples_of(path, context)
    clipped = clipped_runs(samples)
    steps = np.abs(np.diff(samples))
    largest = float(np.percentile(steps, 99.9)) if len(steps) else 0.0
    join = abs(float(samples[-1] - samples[0])) / largest if largest > 0 and len(samples) > 1 else 0.0
    seconds = len(samples) / RATE
    return {"path": path, "lufs": level["lufs"], "true_peak": level["true_peak"], "seconds": seconds,
            "clipped": float(clipped.mean()) if len(clipped) else 0.0, "clipped_mask": clipped, "samples": samples,
            "length_fault": sound_gates(context).length_fault(sound, seconds, file_count), "join": join}


def run(record, context):
    sound = files.record_json(record, context.blobs, "sound.json") or {}
    named = [name for name in files.named(record, "audio")
             if not sound.get("files") or name in sound["files"]]
    folder = files.linked(record, context.blobs, context.work / "sound" / record["id"])
    measured = [dict(measured_file(folder / name, sound, len(named), context), name=name) for name in named]
    if not measured:
        return [parts.scored("files", 0, 0, "the sound has no audio file")]
    found = [clap_part(sound)]
    found.append(loudness_part(sound, measured, context))
    found.append(peak_part(measured, context))
    found.append(clipping_part(measured, context))
    found.append(length_part(sound, measured, context))
    found.append(join_part(sound, measured))
    return found


def clap_part(sound):
    if sound.get("made_by") != "moss":
        return parts.unmeasured("prompt match", "a recording the owner picked: no prompt to match", parts.NOT_JUDGED)
    if sound.get("clap") is None:
        return parts.unmeasured("prompt match", "its record holds no CLAP score")
    return parts.ramped("prompt match", sound["clap"], CLAP, "CLAP score of the take against its prompt, from its "
                        "record (the sound stage's likeness gate: CLAP_FLOOR 0.30)", weight=2.0, unit="CLAP")


def loudness_part(sound, measured, context):
    tool = loudness_tool(context)
    category = sound.get("category")
    target_name = sound_gates(context).picker_takes.TARGET.get(category) if category else None
    if target_name is None:
        return parts.unmeasured("loudness", "no category, so no loudness target")
    target = tool.TARGETS[target_name]
    values = []
    for each in measured:
        off = each["lufs"] - target
        value = parts.ramp(off, *OVER_TARGET) if off >= 0 else parts.ramp(-off, *UNDER_TARGET)
        values.append((value, each))
    value, worst = min(values, key=lambda item: item[0])
    how = (f"integrated loudness {worst['lufs']} LUFS against its {category} target {target} LUFS (ffmpeg ebur128); "
           f"over: 100 at {OVER_TARGET[0]}, 50 at {OVER_TARGET[1]}, 0 at {OVER_TARGET[2]} LU; under: 100 at "
           f"{UNDER_TARGET[0]}, 50 at {UNDER_TARGET[1]}, 0 at {UNDER_TARGET[2]} LU; worst of {len(measured)} files "
           f"({worst['name']})")
    part = parts.scored("loudness", value, round(worst["lufs"] - target, 2), how, unit="LU off target")
    return waved(part, worst, loudest_window(worst["samples"]), context, "its loudest half second shaded")


def loudest_window(samples, seconds=0.5):
    """(start, end) seconds of the loudest window."""
    width = max(int(RATE * seconds), 1)
    if len(samples) <= width:
        return 0.0, len(samples) / RATE
    running = np.concatenate([[0.0], np.cumsum(samples.astype(np.float64) ** 2)])
    energy = running[width:] - running[:-width]
    start = int(energy.argmax())
    return start / RATE, (start + width) / RATE


def peak_part(measured, context):
    worst = max(measured, key=lambda each: each["true_peak"])
    part = parts.ramped("true peak", worst["true_peak"], TRUE_PEAK, f"true peak (ffmpeg ebur128, peak=true) against "
                        f"the -3 dBTP ceiling; worst of {len(measured)} files ({worst['name']})", unit="dBTP")
    peak = int(np.abs(worst["samples"]).argmax()) / RATE if len(worst["samples"]) else 0.0
    return waved(part, worst, (max(peak - 0.05, 0.0), peak + 0.05), context, "its peak shaded")


def clipping_part(measured, context):
    worst = max(measured, key=lambda each: each["clipped"])
    part = parts.ramped("clipping", round(worst["clipped"], 6), CLIPPED, f"share of samples in runs of three or more "
                        f"at full scale; worst of {len(measured)} files ({worst['name']})", unit="share")
    if worst["clipped_mask"].any():
        first = int(np.flatnonzero(worst["clipped_mask"])[0]) / RATE
        return waved(part, worst, (max(first - 0.05, 0.0), first + 0.05), context, "its first clipped run shaded")
    return part


def length_part(sound, measured, context):
    faults = [(each["length_fault"], each) for each in measured if each["length_fault"]]
    how = "the sound stage's length rule against its brief (gates.length_fault)"
    if not faults:
        longest = max(each["seconds"] for each in measured)
        return parts.scored("length", 100, round(longest, 2), how + ": within", unit="s")
    fault, worst = faults[0]
    part = parts.scored("length", 25, round(worst["seconds"], 2), f"{how}: {fault} ({worst['name']}); a fault is 25",
                        unit="s")
    brief = float(sound.get("brief_seconds") or 0.0)
    return waved(part, worst, (min(brief, worst["seconds"]), worst["seconds"]), context, "past its brief shaded")


def join_part(sound, measured):
    if not sound.get("loops"):
        return parts.unmeasured("loop join", "not a loop", parts.NOT_JUDGED)
    worst = max(measured, key=lambda each: each["join"])
    return parts.ramped("loop join", round(worst["join"], 2), LOOP_JOIN, f"the step across the loop's join over the "
                        f"file's 99.9th percentile step ({worst['name']})", unit="ratio")


def waved(part, measured, window, context, what):
    if part["value"] >= 75:
        return part
    part["picture"] = spots.wave_spot(measured["samples"], RATE, window[0], window[1], context.blobs, what)
    return part
