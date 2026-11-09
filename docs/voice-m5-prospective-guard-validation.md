# M5 — prospective acoustic-echo guard validation on a NEW physical take

## Purpose and limits

This protocol is written BEFORE seeing a new physical capture after the
2026-11-09 M5 known-stem and guarded-FIR exploratory results.

Question: Does the existing, unmodified **echo-estimate-only 3800Hz guard**
retain meaningful physical speaker-only echo reduction while avoiding the
negative high-frequency energy effects observed with the unguarded FIR,
on a fresh continuous doubletalk take?

This experiment does NOT independently certify intelligible/undistorted
near-end speech: the single microphone does not supply the simultaneous
clean near-end speech truth. It can validate reproducibility of speaker-
only suppression and conservative upper-band interference behavior
in a separate physical capture with operator voice after calibration.

It is not a production rollout and must not change PipeWire/ALSA defaults,
play back the mic, spawn any background service or enable the CueWire
virtual microphone. M4 LEVEL-001 stays immutable.

## Pre-registered configuration — no tuning after this document

- Hardware: existing explicit USB physical MICAudio-97i source, physically
  unchanged position, powered USB hub; explicit physical analogue speaker
  sink \`alsa_output.pci-0000_00_1f.3.analog-stereo\`.
- Sample rate: 48kHz mono signed PCM16.
- Reference: existing local music file
  \`/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav\`,
  converted to **exactly 32s** by \`tools/voice_m5_capture.py\`.
  Reusing the same digital song tests *new take reproducibility*, not
  generalization to different music, rooms or playback chains.
- Playback volume: \`0.30\`; do not change other audio during this take.
- Silence and speech: **first 12s of playback** must contain speaker
  only, with operator silent; **next ~20s** operator speaks normally
  over music when explicitly prompted. Fixed microphone/speaker position.
- Alignment: existing \`voice_m5_doubletalk_gate.lag_estimate\` on early
  speaker-only samples; this is not hardware time sync.
- FIR: frozen **120ms, 5760 taps, 8192 Welch/FFT, ridge 0.02**,
  10ms pre-peak shift, 8ms tail fade; gain is set from training only.
- Train: digital reference **3–9s**.
- Speaker-only validation (unused in fit): digital reference **10–11.5s**;
  ends before 12s voice prompt.
- Mixed speech/music diagnostic: reference time **13–28s**;
  NOT a clean-voice quality measurement.
- Original benchmark: subtract whole-band echo estimate.
- Guarded variant: same prediction, then **513-tap symmetric Hann
  zero-phase FIR at 3800Hz** before subtraction; the physical
  microphone is not filtered. This is an OFFLINE NONCAUSAL
  research filter, not a ready-to-run real-time implementation.

Scripts: \`tools/voice_m5_capture.py\` and
\`tools/voice_m5_guarded_fir.py\`. Do not change numerical settings
or code in response to results from this prospective take. Changes
required to fix a code bug must be disclosed, tested, and may require
a separate fresh take for independent evaluation.

## Acceptance and interpretation — fixed BEFORE observing new data

### Data-integrity prerequisites (otherwise INVALID)

- Capture \`status=captured_for_offline_aec\`, 12s calibration, 32s
  reference, full-length mic, zero clipped samples.
- Explicit physical microphone and sink, no route changes, no other
  playback during the experiment.
- Digital reference and physical mic SHA256 hashes must match the
  new capture manifest and remain unchanged through offline analysis.
- **New mic raw SHA256 MUST differ** from the earlier doubletalk-001
  recording \`f6ce01ba66b9e68121c535ff1ddd735001e4381f9028e9fcb7032da420ed24c8\`.
- Operator must actually remain silent during first 12s; the manifest
  alone cannot prove this. Any known interruption invalidates the
  trial; disclose rather than filtering away a bad portion.

### Quantitative research gate (NOT a voice-quality PASS)

Apply thresholds to the **new** speaker-only 10–11.5s holdout
from the guarded variant:

1. Guarded overall heldout energy reduction (ERLE) **>=1.0 dB**.
2. Guarded 500–1500Hz speaker-only ERLE **>=3.0 dB**.
3. Guarded 1500–4000Hz speaker-only ERLE **>=3.0 dB**.
4. Guarded 4000–8000Hz speaker-only ERLE **>=-0.25 dB**.
5. Guarded 8000–16000Hz speaker-only ERLE **>=-0.25 dB**.
6. On the 13–28s speech+music mixture, guarded 4000–8000Hz
   and 8000–16000Hz energy changes each **>=-0.25 dB**.
   These two checks prevent strong high-band amplification;
   they do NOT prove near-end speech was preserved.

The earlier fullband model's existing 1dB speaker-only gate
must pass for \`voice_m5_guarded_fir.py\` to emit mixed-speech
diagnostics. If it fails, declare the trial inconclusive/rejected
and do not bypass the gate to obtain speech numbers.

These are engineering diagnostics chosen from prior
experiments, **not scientifically validated thresholds** for
human voice quality or echo audibility. The 3800Hz guard
mathematically removes most predicted echo above ~5kHz,
so passing high-band non-amplification is useful mainly as
an implementation/integrity check. Do not mistake it for
independent acoustic speech-preservation evidence.

Also report, without gate-based selective omission, all
six-band ERLE and coherence changes for both original and
guarded estimates, and the mixing total. Do not discard
negative results; no post-hoc optimization on the holdout.

## Procedure (new session; manually initiated only)

1. Verify \`M5-doubletalk-002\` is unused. If already used,
   pick a fresh identifier and keep that same name below.
2. Run local tests and abort if red:

~~~bash
cd /data/cue-wire
git pull --ff-only
~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v
~~~

3. Record one fresh 32s capture:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_capture.py \
  --music '/data/oae-research/ACDC_-_Thunderstruck_(Official_Video).wav' \
  --capture-backend pipewire \
  --source 'alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback' \
  --speaker 'alsa_output.pci-0000_00_1f.3.analog-stereo' \
  --seconds 32 \
  --calibration-seconds 12 \
  --volume 0.30 \
  --out .audit/voice-aec/M5-doubletalk-002
~~~

4. Run the already-frozen, offline guard comparison to a NEW output:

~~~bash
~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_guarded_fir.py \
  --capture .audit/voice-aec/M5-doubletalk-002 \
  --out .audit/voice-aec/M5-guarded-fir-prospective-002

cd /data/cue-wire/.audit/voice-aec/M5-guarded-fir-prospective-002
sha256sum -c SHA256SUMS
~~~

5. Share the new \`manifest.json\` and \`guarded-fir.json\`
plus \`SHA256SUMS\` only. Do NOT upload private RAW music
or mic WAV unless needed for a separate explicitly approved
inspection. Evaluation can be derived from the numeric report.

## What it will NOT establish

- Clean-voice preservation in real doubletalk (requires a different
  physical reference technique or carefully qualified listening test).
- End-to-end game/Steam/CueWire latency or low-latency realizability
  of the offline noncausal filter.
- Generalization to another song, placement, speaker, environment,
  or microphone gain.
- AEC production readiness; production remains explicitly not approved.


## Result — NEW prospective physical take, 2026-11-09 (locked criteria)

The user uploaded the new \`guarded-fir.json\` and matching
\`SHA256SUMS\`. SHA256 independently recalculated from the uploaded JSON:
\`c6fb1cd3bf9275d1d7e0206e6dafebfe8ba57d4c8e7cfc6a247a2aa5bffe1106\`.
The report's new microphone RAW digest is
\`5a35b112abb8de9f7701ea990c87ce2961f1dc449fdf9fff2589ac13c78e8811\`,
different from the former \`M5-doubletalk-001\`
\`f6ce01ba66b9e68121c535ff1ddd735001e4381f9028e9fcb7032da420ed24c8\`.
Its new capture manifest digest is
\`7664960264e0117a110e7b62f6a2d137b52578012569ee50fcc26fe34ea92c77\`;
the 32s digital reference digest remains
\`7661d6412ec346a79db5781ed2bef20fa55a66578a00e5852c5b775451c8b685\`.

The new manifest and original WAVs were not uploaded,
so only report-side hash identities, not independent
on-disk RAW audio integrity, could be checked here.

**The prospective result FAILS the predeclared gate.**
Do not change, waive, or backfill any of the thresholds.

| Prespecified guarded metric | Result | Requirement | Verdict |
| --- | ---: | ---: | --- |
| Speaker-only whole-band heldout ERLE | **0.67881 dB** | >=1 dB | **FAIL** |
| 500–1500 Hz | 3.07699 dB | >=3 dB | PASS |
| 1500–4000 Hz | 3.18032 dB | >=3 dB | PASS |
| 4000–8000 Hz | +0.00575 dB | >=-0.25 dB | PASS |
| 8000–16000 Hz | +0.00002 dB | >=-0.25 dB | PASS |
| 4–16kHz during actual doubletalk | NOT COMPUTED | >=-0.25 dB | NOT TESTED |

The existing original fullband AEC gate also **fails**:
full-FIR heldout ERLE **0.67956 dB**, below 1dB,
so \`double_talk=null\` is the **correct fail-closed behavior**.
Voice quality was neither validated nor rejected by this
speaker-only result; \`approved_for_virtual_mic=false\`.

Calibration/fit degradation is substantial and appears
**already in training**: training ERLE **0.58930 dB**
vs **6.37718 dB** on the prior M5-doubletalk-001 take.
Normalized early reference/microphone lag correlation
**0.111581** vs **0.310195**. Estimated broad-band lag
**281.29167 ms** vs **97.70833 ms**. The lag includes
software/audio-buffer start as well as acoustic propagation;
its change does **not** prove changed physical propagation
or clock drift. Without the new raw WAV and its timing
metadata, the source of degraded correlation is unknown.
Possibilities include weaker speaker leakage relative to
mic background, changed physical/device gain, buffer/route
effects, interfering audio, or an ambiguous correlation
peak. These remain hypotheses, not measured causes.

**Important classification:** the analyzer still emits
\`status=exploratory_reused_holdout_not_production_approved\`,
a generic status string inherited from the prior
same-capture exploration. The physical microphone WAV
hash and the prospectively locked protocol demonstrate
that this is a **new-take prospective FAIL**, not a
reused old capture. The report status label is stale;
do not silently reinterpret it as a PASS or change
the original numeric report. Any later code/metadata
fix is for future studies only.

The echo-only high-frequency guard is encouraging
for *preventing high-band amplification* on this take,
but not sufficient for full-band acceptance and
not validated on simultaneous clean near-end speech.

**Immediate next task:** a separate, immutable, numeric
*postmortem* on the SAME speaker-only 0–9s calibration,
looking for unstable correlation peaks across early
windows and speaker-to-mic level variation. It must
never alter the 0.67881 dB heldout result or reuse
10–11.5s to optimize alignment. Sharing the new
capture's \`manifest.json\` would also permit checking
device, gain and playback timestamps against prior
runs.


### Read-only diagnostic after the FAILED new take

New \`tools/voice_m5_lag_postmortem.py\` was added after
the negative result. It is **not** part of the registered
acceptance test and does not modify the result or tune the
model.

It requires the original new-take manifest, both physically
recorded WAVs under the existing local \`.audit\` directory,
and the original already-failed \`guarded-fir.json\`. It
SHA256-checks both WAVs and the manifest against the failed
report, and refuses if those files differ or the earlier
1dB gate did not FAIL.

It computes correlations for three EARLY
speaker-only reference intervals **0.8–2.3, 3.0–4.5, and
6.0–7.5 seconds**, in the full band, 500–1500Hz
and 1500–4000Hz. For each it reports normalized
correlation at the ORIGINAL committed lag (**281.29167ms**)
and the top three separated lag peaks in the
fixed 0–2s search. The source material used for
these extra probes ends before the operator starts
speaking; it neither fits an acoustic filter nor
uses the 10–11.5s validation interval.

**The diagnostic peaks must never be substituted back
into this prospective result to convert FAIL to PASS.**
Their purpose is to determine whether the original lag
was consistent across several early observation windows,
and whether speaker-reference correlation changed
between bands. Ambiguous music-correlations and
process-start jitter are recognized limitations.

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_lag_postmortem.py \
  --capture .audit/voice-aec/M5-doubletalk-002 \
  --report .audit/voice-aec/M5-guarded-fir-prospective-002/guarded-fir.json \
  --out .audit/voice-aec/M5-lag-postmortem-002

cd .audit/voice-aec/M5-lag-postmortem-002
sha256sum -c SHA256SUMS
~~~

The example report folder assumes the documented
\`M5-guarded-fir-prospective-002\` name was actually used;
if different, locate the existing \`guarded-fir.json\`
and replace ONLY \`--report\` with its real path.
Likewise use the actual capture folder and NEW output
identifier. Do not overwrite or move prior records.
The new tool outputs only \`lag-postmortem.json\`
and \`SHA256SUMS\`.

Two synthetic tests were added (31 total in
\`tests/test_voice_m5_aec.py\`), checking that a known
lag is recovered on an independent early interval,
input files are unmodified, no WAV is written, and
an already failed original gate is not overridden.
Local tests have not yet been executed.

Please also provide the new capture's actual
\`M5-doubletalk-002/manifest.json\` to audit source,
playback duration, physical microphone and timing
metadata. No private WAV upload is needed for that
manifest-level check.


### Offline cross-take calibration comparison — fixed and source-hash-pinned

Added \`tools/voice_m5_early_cross_take.py\` to compare
**only reference-time 0.8–2.3s, 3.0–4.5s and 6.0–7.5s**
from the **same digitally identical music reference**
in the two physical recordings. The analysis addresses the
hypothesis of changed physical mic-level/background versus
changed linear correlation with the speaker source, but
cannot definitively separate electronic noise, room noise,
acoustic path transfer and microphone firmware AGC.

Inputs are strictly pinned to the SHA256s of the two
original physical capture manifests, both microphone RAW WAVs
and the identical exact digital PCM reference. Both captured
devices/volume must also match; the code refuses any modified
file and any existing output directory. The original
**4690-frame** and prospective **13502-frame**
correlation delays are only used as fixed data indices.
There is NO lag search, FIR fitting, gain optimization,
AEC subtractions, validation-holdout inspection, doubletalk
analysis, voice export or audio output.

Report for each early speaker-only window:

- Original music RMS, mic RMS and physical mic peak;
- observed zero-shift signed reference/mic correlation
  at each take's original frozen timing;
- per-band source/mic RMS levels, mic-PSD-weighted
  magnitude-squared spectral coherence for 20–70,
  70–180, 180–500, 500–1500, 1500–4000, 4000–8000
  and 8000–16000Hz;
- per-band *new minus old* mic RMS and coherence
  differences; no inferred clean voice quality.

All three windows avoid the heldout speaker-only
**10–11.5s** interval; results cannot retroactively
change the prospectively registered **0.67881 dB FAIL**.
Raw PCM input remains private and is not committed.

Run the **33** regression tests (two new cross-take tests
verify source-pin failure, tampering, and numeric-only
output) then run the offline diagnostic:

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_early_cross_take.py \
  --old-capture .audit/voice-aec/M5-doubletalk-001 \
  --new-capture .audit/voice-aec/M5-doubletalk-002 \
  --out .audit/voice-aec/M5-cross-take-calibration-001

cd .audit/voice-aec/M5-cross-take-calibration-001
sha256sum -c SHA256SUMS
~~~

No additional physical recording is necessary. The output
contains only \`cross-take-calibration.json\` and
\`SHA256SUMS\`. If \`M5-cross-take-calibration-001\`
already exists, choose a new output folder; never
overwrite or edit prior numerical experiments.
**Tests have not yet been executed on the user's machine**.
Share the JSON report for the next review.

### Physical capture manifest comparison: doubletalk-001 vs doubletalk-002

The user supplied the **actual original M5-doubletalk-002 manifest**,
independently SHA256-checked against the earlier failed guarded report:
\`7664960264e0117a110e7b62f6a2d137b52578012569ee50fcc26fe34ea92c77\`.
The earlier **successful** M5-doubletalk-001 manifest is also available:
\`6e3a33caf73620e718d2e378a8daec96800e24712477fde932cffecaaaa7bb83\`.
This is a new report-side comparison; no previous result is modified.

| Capture metadata | First real doubletalk-001 | New prospective doubletalk-002 |
| --- | ---: | ---: |
| Recorder start UTC | 2026-11-09 00:25:23.478539 | 2026-11-09 02:31:30.519506 |
| pw-play launch UTC (approx) | 00:25:23.586263 | 02:31:30.799314 |
| Launch minus recorder start | **107.724 ms** | **279.808 ms** |
| Acoustic/reference lag from frozen estimator | **97.70833 ms** | **281.29167 ms** |
| Estimated lag minus wall-clock launch interval | **-10.01567 ms** | **+1.48367 ms** |
| pw-play wall duration | **32.073 s** | **32.291 s** |
| pw-record final WAV duration | **32.533333 s** | **32.725333 s** |
| Whole-take physical mic RMS | -40.781 dBFS | -42.379 dBFS |
| Whole-take mic peak | -17.501 dBFS | -17.825 dBFS |
| Mic clipping | 0 | 0 |
| Frozen FIR train ERLE | +6.37718 dB | +0.58930 dB |
| Frozen FIR speaker-only heldout ERLE | +5.73176 dB | +0.67956 dB |
| Guarded speaker-only heldout ERLE | +5.63696 dB | **+0.67881 dB — FAIL** |

**Exactly matching identifiers/settings in both manifests:**

- \`mic_source_name=alsa_input.usb-Anlya.cn_MICAudio-97i_08613544166503-00.mono-fallback\`,
  PipeWire \`mic_source_id_at_start=10788\`,
  \`backend=pipewire\`, \`s16le 1ch 48000Hz\`.
- Sink \`alsa_output.pci-0000_00_1f.3.analog-stereo\`,
  \`speaker_sink_id_at_start=1836\`, \`volume_pw_play=0.30\`.
- Same exact 32s digital reference SHA256
  \`7661d6412ec346a79db5781ed2bef20fa55a66578a00e5852c5b775451c8b685\`.
- Same original music input and 12s planned speaker-only interval.
- Successful player returncode 0; recorder returncode 1 after controlled
  termination with valid finalized RAW WAV and no clipping.
- Distinct microphone RAW SHA256s; the prospective take is not a duplicate.

**Timing insight:** the estimated reference-to-mic lag increased
by **183.58334 ms** between takes, while the relative wall-clock
player-launch interval grew **172.084 ms**. Their difference is
**11.49934 ms**. Thus roughly 93.7% of the *numerical*
lag change tracks variable process startup times. The remaining
difference is not proven acoustic path length or clock drift;
wall-clock process launch is not a DAC or ADC sample timestamp,
and the musical signal has multiple approximately 8ms-separated
correlation peaks. The first lag even precedes its approximate
logged playback launch by 10ms, demonstrating why these
different timebases cannot be treated as direct hardware events.

The new manifest records microphone bytes observed during
playback at 0.1607s, versus 0.1206s in the successful
capture, but those are **file-buffer readiness indicators**,
not calibrated microphone acoustic onset times.

The whole-take microphone RMS differs by -1.598 dB,
but includes 20s of user speech after the 12s music-only
calibration; this is **not a valid comparison of echo
level, mic hardware gain, or environmental noise floor**.
No independent gain setting, mixer graph state, physical
mic/speaker positions or AGC behavior was logged. Identical
node IDs and fixed pw-play volume do not establish identical
physical acoustic conditions.

**Interpretation:** variable recorder/player startup accounts
for much of the difference in absolute lag, whereas the
serious reduction in acoustic model performance appears
already within training. Do not claim that an overall
183ms lag jump caused the 0.67881-dB failure; the original
fit re-estimated that lag. The mechanism remains unresolved.

**Next safe diagnostic:** compare predeclared early 3–9s
speaker-only *frequency-resolved mic/reference power and
linear coherence* between the two SHA256-verified physical
mic WAVs. Do not use the heldout 10–11.5s or
doubletalk 13–28s to reselect alignment or model. Keep
the failed gate and all historical reports immutable.

### Corrected M5-lag-postmortem-003: actual 27 peaks, checksum verified

The user reran the diagnostic after the 3ms/3s exclusion
bug was fixed. Both uploaded \`lag-postmortem.json\` and
\`SHA256SUMS\` were read; the independently computed
report SHA256 agrees exactly:

\`81615df066df8a740162f13ae972f880c30ad6bdd8eaeedb9262b9416e3d38d7\`.

- The corrected report has **3 separated peaks in each of 9
  early-window × spectral-band combinations** (27 total).
  All entries have nonnull first-vs-second margins.
- The immutable source and original failed guarded report
  digests match the preceding prospective failed capture,
  so this is not a new physical recording or model refit.
- The **0.67881 dB** guarded wholeband speaker-only heldout
  ERLE remains an unmodified **FAIL**.

**Top-two correlation peaks** (lag in milliseconds, absolute
normalized correlation; source report retains signed values):

| Band | 0.8–2.3s | 3.0–4.5s | 6.0–7.5s |
| --- | --- | --- | --- |
| Fullband | 281.3125 (.1211), 294.3958 (.0899) | 281.2917 (.1354), 294.3750 (.0905) | 281.0000 (.1328), 291.1875 (.1278) |
| 500–1500Hz | 282.2708 (.3024), 290.3958 (.2766) | 282.2083 (.2501), 290.3333 (.2409) | 282.1667 (.3781), 306.4792 (.3317) |
| 1500–4000Hz | 281.2917 (.3883), 289.3750 (.2590) | 281.2708 (.4086), 289.1458 (.2787) | 281.2500 (.3514), 285.3333 (.2843) |

The 1500–4000Hz primary lag varies by only
**0.04167ms** (two 48kHz samples) among early windows.
Absolute correlation advantage over the next
3ms-separated peak is **0.129378, 0.129877,
0.067132**: measurable, but the peak is not
"unambiguously unique" in a statistical sense.
In 500–1500Hz the corresponding advantage is only
**0.025832, 0.009160, 0.046436**, so substantial
alternate candidates exist.

First two low-mid peaks on windows 0.8–2.3s and
3.0–4.5s differ by **8.125ms**; the top high-mid
peaks in the first two windows differ by
**8.08333ms** and **7.875ms**. A periodic
source with period ~8.1ms has repeating structure
around **123Hz**, compatible with musical pitch/
harmonics; it is NOT a measured separate room
reflection or the confirmed cause of the
underperforming AEC. Frequency-dependent
filtering/group delay and room response may
also contribute to peak shifts. The overall
broadband correlations remain low (~0.12–0.14),
and the three windows do not prove hardware
clock synchronization or rule out slow phase
variation.

Crucial nuance: on the third fullband window the
top peak is **281.0000ms (positive r +0.132763)**,
while original committed lag 281.29167ms has
**negative r -0.116396**. A few samples can
change the sign of a broadband music correlation;
the frequency-limited peaks are more stable.
This is a measurement of the actual alignment
objective, not a proposal to retime the already
frozen model.

**Interpretation preserved:** (1) diagnostic code
now produces the intended complete alternative-peak
list; (2) stable high-mid primary lag suggests that
a large early timing jump is not the simplest
explanation; (3) other bands and broadband have
competing harmonic peaks; (4) no cause of low
wholeband ERLE is yet established. Avoid selecting
the best peak from these data to retroactively
convert a failed prospective holdout into a PASS.

The next needed evidence is the **actual new capture's
\`M5-doubletalk-002/manifest.json\`**, not another
WAV recording. The report itself lists only
source digests and timing/correlation statistics;
it lacks the capture's device/gain/playback
metadata needed to check the hardware/session
conditions against the prior 5.73176dB successful
take. Without that manifest, do not claim a
particular hub, gain setting, mic position,
PipeWire route, firmware AGC, or speaker level
caused the difference.

### Postmortem alternative-peak calculation bug — fixed, 2026-11-09

The first uploaded lag postmortem returned only ONE
\`diagnostic_top_separated_peaks\` candidate in all nine
band/window combinations, with a null runner-up margin.
A new regression assertion reproduced it locally:
\`AssertionError: 1 != 3\` inside
\`test_m5_lag_postmortem_known_fixed_lag_early_windows\`
(31 tests, 1 failure).

**Root cause confirmed in code:**

~~~python
PEAK_SEPARATION_MS = 3.0
# Wrong: uses 3 seconds rather than 3 milliseconds (144000 frames).
separation = round(PEAK_SEPARATION_MS * RATE)
# Correct: 3 milliseconds / 1000 seconds = 144 PCM frames.
separation = round(PEAK_SEPARATION_MS * RATE / 1000)
~~~

The old logic blacklisted a +/-3-second interval around
the first correlation maximum, eliminating the ENTIRE
0–2 second candidate range after one iteration. No
second or third peak could be reported, even on clean
synthetic signals. The earlier JSON remains immutable
and valid only for its leading peak and committed-lag
metrics. Its missing runner-up evidence is a SOFTWARE
BUG, not evidence of a uniquely identified lag.

The fix changes only **how diagnostic alternative
peaks are collected**. It does not change input PCM,
original frozen AEC coefficients, physical capture,
the leading correlation maximum, or the previously
failed **0.67881dB** speaker-only gate.

The synthetic test now requires THREE peaks separated
by at least 3ms (allowing for five-decimal rounding);
the full local suite still needs to be rerun.

**Repeat diagnostic under a fresh output ID**:

~~~bash
cd /data/cue-wire
git pull --ff-only

~/.venvs/cuewire-voice-lab/bin/python \
  -m unittest discover -s tests -p test_voice_m5_aec.py -v

~/.venvs/cuewire-voice-lab/bin/python \
  tools/voice_m5_lag_postmortem.py \
  --capture .audit/voice-aec/M5-doubletalk-002 \
  --report .audit/voice-aec/M5-guarded-fir-prospective-002/guarded-fir.json \
  --out .audit/voice-aec/M5-lag-postmortem-003

cd /data/cue-wire/.audit/voice-aec/M5-lag-postmortem-003
sha256sum -c SHA256SUMS
~~~

Use the **actual** local report/capture paths if
different. Do not overwrite the earlier postmortem.
Compare the top 3 peaks for each early window; do
not use them to retime or relabel the failed heldout.
If the corrected local unit tests are green, send
\`M5-lag-postmortem-003/lag-postmortem.json\` and
its \`SHA256SUMS\`. No recordings are needed.

### Completed early-window postmortem: actual verified lag-postmortem.json (2026-11-09)

The user supplied the real \`lag-postmortem.json\` and
\`SHA256SUMS\`. Their SHA256 agrees:
\`0c138d2934e90f05b0a88af2b2c114dc1b4bc46b77c502d12088d111e497e4b9\`.
Its \`input_sha256.original_guarded_report\` is
\`c6fb1cd3bf9275d1d7e0206e6dafebfe8ba57d4c8e7cfc6a247a2aa5bffe1106\`,
identical to the original failed prospective report.
New microphone and manifest SHA256s likewise match
the original failed take. No new model coefficients or
holdout estimate were computed.

The frozen original lag is **281.29167 ms**.

| Early digital reference interval | Fullband top lag / signed r | 500–1500Hz top lag / signed r | 1500–4000Hz top lag / signed r |
| --- | --- | --- | --- |
| 0.8–2.3 s | 281.31250ms / -0.121093 | 282.27083ms / +0.302411 | 281.29167ms / -0.388348 |
| 3.0–4.5 s | 281.29167ms / -0.135409 | 282.20833ms / +0.250067 | 281.27083ms / -0.408608 |
| 6.0–7.5 s | 281.00000ms / +0.132763 | 282.16667ms / +0.378132 | 281.25000ms / -0.351448 |

The **1500–4000Hz** peak varies by only **0.04167ms**
(two 48kHz PCM frames) across the three early windows.
The **500–1500Hz** peaks span **0.10416ms** and sit
about **0.92–0.98ms later** than the high-mid peaks.
At the originally frozen 281.29167ms lag, the
500–1500Hz normalized correlations are small
\`-0.006273, +0.029120, -0.048520\`, although the
best independently observed midband peak is
\`0.250–0.378\`. Thus a single broadband lag does not
necessarily maximize cross-correlation in every band.
This frequency dependence could arise from room
reflections, group delay, music periodicity or mic
signal processing. It does NOT prove one physical cause.

The **fullband** original-lag absolute correlation
is low and stable, **0.11665, 0.135409, 0.116396**.
The same three original-lag mic RMS levels are
**-59.02826, -59.04974, -58.56572 dBFS**,
even though digital reference fullband RMS stays
around -27 to -28 dBFS. This supports the concern
that overall broadband mic energy may contain large
reference-independent components, but digital reference
and physical mic have different signal-chain gains,
so their absolute RMS difference is **not a measured
acoustic attenuation factor or echo fraction**.

**Interpretation:** on this take there is no evidence of
a large, rapidly shifting high-mid correlation peak
during early calibration. We have not ruled out
small clock drift, time-varying phase/reverberation,
microphone gain/DSP, weak echo-to-noise ratio,
or an in-band fitting/regularization issue.
This postmortem is NOT a retiming exercise. It
does NOT rescue the previously failed **0.67881 dB**
heldout speaker-only gate.

**Diagnostic report completeness caveat:** the code version
in GitHub is specified to return the top three lag peaks
separated by at least 3ms, but every supplied
\`diagnostic_top_separated_peaks\` array contains
only **one** entry; consequently
\`first_minus_second_peak_abs_correlation=null\`.
This means we cannot infer that the reported top
peak is unique or separated strongly from other
competing lag candidates. A clean synthetic-input
unit test returns multiple peaks using the intended
algorithm, so the discrepancy is worth investigating
without altering the historical JSON. Verify actual
local code version before claiming peak uniqueness.

The next useful evidence is the actual
\`M5-doubletalk-002/manifest.json\` of this failed
prospective take: compare physical endpoint, playback
wall time, recorder diagnostics, mic peak/RMS and
capture startup to previously successful runs.
Even then the capture manifest alone cannot reveal
the exact noise/echo origin. Neither a new audio
recording nor any coefficient tuning is necessary
for this metadata comparison.
