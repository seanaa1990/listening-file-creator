import streamlit as st
import edge_tts
import asyncio
import tempfile
import os
import subprocess
import re
import struct
import math
import shutil
import hashlib
from typing import List, Tuple, Optional


def _patch_edge_tts_for_v2():
    """Workaround for Microsoft's October 2026 Sec-MS-GEC v2 requirement.
    Fixes WSServerHandshakeError: 403 in edge-tts <= 7.2.8.
    Automatically no-ops if a future edge-tts release already uses v2.
    """
    import edge_tts
    if not getattr(edge_tts.constants, "SEC_MS_GEC_VERSION", "").startswith("1-"):
        return

    from edge_tts.constants import TRUSTED_CLIENT_TOKEN, CHROMIUM_FULL_VERSION
    from edge_tts.drm import DRM, WIN_EPOCH, S_TO_NS

    PATENT_HASH_PREFIX = b"Copyright (C) Microsoft Corporation. All rights reserved."
    _u32_mask = 0xFFFFFFFF

    def _u32(value: int) -> int:
        return value & _u32_mask

    def _rol16(value: int) -> int:
        value &= _u32_mask
        return ((value << 16) | (value >> 16)) & _u32_mask

    def _words_le4_even(data: bytes) -> List[int]:
        padded = data + b"\x00" * ((-len(data)) % 4)
        if not padded:
            return []
        words = list(struct.unpack(f"<{len(padded) // 4}I", padded))
        if len(words) % 2:
            words.append(0)
        return words

    def _cs64_word_swap(words: List[int], key_words: List[int]) -> Tuple[int, int]:
        if len(words) < 2 or len(words) % 2 or len(key_words) < 2:
            raise ValueError("CS64 requires nonempty even-sized data and 2 key words")
        a, b = key_words[0] | 1, key_words[1] | 1
        acc = 0
        last = 0
        for x, y in zip(words[0::2], words[1::2]):
            t = _u32(_rol16(x) * 0xEF0569FB + x * a)
            t = _rol16(t)
            q = _u32(t * 0x76F50F34)
            q = _u32(_rol16(q) * 0xC3EFEA97 + q)
            acc = _u32(acc + q)

            t = _u32(_rol16(y) * 0xC31713DB + y * b)
            t = _rol16(t)
            q = _u32(t * 0x1881CE3C)
            last = _u32(_rol16(q) * 0x35BD1EC9 + q)
            acc = _u32(acc + last)
        return last, acc

    def _cs64_reversible(words: List[int], key_words: List[int]) -> Tuple[int, int]:
        if len(words) < 2 or len(words) % 2 or len(key_words) < 2:
            raise ValueError("CS64 requires nonempty even-sized data and 2 key words")
        a, b = key_words[0] | 1, key_words[1] | 1
        state = acc = 0
        for x, y in zip(words[0::2], words[1::2]):
            state = _u32((state + x) * a)
            for const in (0xCF98B111, 0x87085B9F, 0x12CEB96D, 0x257E1D83):
                state = _u32(_rol16(state) * const)
            t = _u32((y + state) * b)
            t = _u32(_rol16(t) * 0xA27416F5)
            t = _u32(_rol16(t) * 0xD38396FF)
            t = _rol16(t)
            acc = _u32(acc + state)
            state = _u32(_rol16(_u32(t * 0x7C932B89)) * 0xBFA49F69)
            acc = _u32(acc + state)
        return state, acc

    def _patent_hash(data: bytes) -> Tuple[str, str]:
        digest = hashlib.sha256(data).digest()
        data_words = _words_le4_even(data)
        digest_words = _words_le4_even(digest)
        a = _cs64_word_swap(data_words, digest_words)
        b = _cs64_reversible(data_words, digest_words)
        cs64 = struct.pack("<II", a[0] ^ b[0], a[1] ^ b[1]).hex().upper()
        return cs64, digest.hex().upper()

    def generate_sec_ms_gec_v2(unix_timestamp: Optional[float] = None) -> str:
        if unix_timestamp is None:
            ticks = DRM.get_unix_timestamp()
        else:
            ticks = float(unix_timestamp)
        ticks += WIN_EPOCH
        ticks -= ticks % 300
        ticks *= S_TO_NS / 100
        str_to_hash = f"{ticks:.0f}{TRUSTED_CLIENT_TOKEN}".encode("ascii")
        cs64, sha256 = _patent_hash(PATENT_HASH_PREFIX + str_to_hash)
        return f"{cs64}-{sha256}"

    v2_version = f"2-{CHROMIUM_FULL_VERSION}"
    edge_tts.constants.SEC_MS_GEC_VERSION = v2_version
    import edge_tts.communicate
    edge_tts.communicate.SEC_MS_GEC_VERSION = v2_version
    import edge_tts.voices
    edge_tts.voices.SEC_MS_GEC_VERSION = v2_version
    edge_tts.drm.DRM.generate_sec_ms_gec = staticmethod(generate_sec_ms_gec_v2)


_patch_edge_tts_for_v2()


def _resolve_ffmpeg():
    """Prefer the pip-installed static binary; fall back to a system ffmpeg."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


FFMPEG = _resolve_ffmpeg()

st.set_page_config(page_title="Listening File Creator", page_icon="🎧", layout="centered")

st.title("🎧 Listening File Creator")

VOICES = {
    # ── 🇬🇧 United Kingdom ──
    "🇬🇧 British Female (Sonia) — clear, neutral": "en-GB-SoniaNeural",
    "🇬🇧 British Female (Maisie) — younger sound": "en-GB-MaisieNeural",
    "🇬🇧 British Female (Libby)": "en-GB-LibbyNeural",
    "🇬🇧 British Male (Ryan) — warm, natural": "en-GB-RyanNeural",
    # ── 🇺🇸 United States ──
    "🇺🇸 American Female (Jenny) — friendly": "en-US-JennyNeural",
    "🇺🇸 American Female (Aria) — confident": "en-US-AriaNeural",
    "🇺🇸 American Female (Ava) — expressive, warm": "en-US-AvaMultilingualNeural",
    "🇺🇸 American Female (Emma) — cheerful, clear": "en-US-EmmaMultilingualNeural",
    "🇺🇸 American Female (Michelle) — pleasant": "en-US-MichelleNeural",
    "🇺🇸 American Male (Guy) — clear": "en-US-GuyNeural",
    "🇺🇸 American Male (Andrew) — warm, confident": "en-US-AndrewMultilingualNeural",
    "🇺🇸 American Male (Brian) — casual, approachable": "en-US-BrianMultilingualNeural",
    "🇺🇸 American Male (Christopher) — authoritative": "en-US-ChristopherNeural",
    "🇺🇸 American Male (Eric) — measured": "en-US-EricNeural",
    "🇺🇸 American Male (Roger) — lively": "en-US-RogerNeural",
    "🇺🇸 American Male (Steffan) — steady, formal": "en-US-SteffanNeural",
    # ── 🇦🇺 Australia ──
    "🇦🇺 Australian Female (Natasha)": "en-AU-NatashaNeural",
    "🇦🇺 Australian Male (William)": "en-AU-WilliamMultilingualNeural",
    # ── 🇨🇦 Canada ──
    "🇨🇦 Canadian Female (Clara)": "en-CA-ClaraNeural",
    # ── 🇮🇳 India ──
    "🇮🇳 Indian Female (Neerja)": "en-IN-NeerjaNeural",
    "🇮🇳 Indian Male (Prabhat)": "en-IN-PrabhatNeural",
    # ── 🇸🇬 Singapore ──
    "🇸🇬 Singapore Female (Luna)": "en-SG-LunaNeural",
    # ── 🌍 African Englishes ──
    "🇰🇪 Kenyan Female (Asilia)": "en-KE-AsiliaNeural",
    "🇰🇪 Kenyan Male (Chilemba)": "en-KE-ChilembaNeural",
    "🇳🇬 Nigerian Female (Ezinne)": "en-NG-EzinneNeural",
    "🇳🇬 Nigerian Male (Abeo)": "en-NG-AbeoNeural",
    "🇹🇿 Tanzanian Female (Imani)": "en-TZ-ImaniNeural",
    "🇹🇿 Tanzanian Male (Elimu)": "en-TZ-ElimuNeural",
    "🇿🇦 South African Female (Leah)": "en-ZA-LeahNeural",
    "🇿🇦 South African Male (Luke)": "en-ZA-LukeNeural",
}

ANNOUNCEMENT_VOICES = {
    "🇬🇧 British Female (Sonia)": "en-GB-SoniaNeural",
    "🇬🇧 British Male (Ryan)": "en-GB-RyanNeural",
    "🇺🇸 American Female (Jenny)": "en-US-JennyNeural",
    "🇺🇸 American Male (Guy)": "en-US-GuyNeural",
}

# Keyed by voice id rather than list position, so reordering VOICES above
# can never silently reassign a speaker's default voice.
DEFAULT_SPEAKER_VOICES = [
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-AU-NatashaNeural",
    "en-AU-WilliamMultilingualNeural",
]
voice_list = list(VOICES.keys())
_voice_ids = list(VOICES.values())

async def generate_clip(text, voice, rate_pct, pitch_hz, out_path):
    rate_str = f"{rate_pct - 100:+d}%"
    pitch_str = f"{pitch_hz:+d}Hz"
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    await communicate.save(out_path)


# Cap on simultaneous requests to the TTS endpoint. Unbounded fan-out on a long
# script risks being rate limited, which is slower than just queueing.
MAX_PARALLEL_CLIPS = 5


async def _generate_all(jobs, on_progress=None):
    """Synthesize (text, voice, rate, pitch, out_path) jobs concurrently."""
    sem = asyncio.Semaphore(MAX_PARALLEL_CLIPS)
    done = 0

    async def one(job):
        nonlocal done
        async with sem:
            await generate_clip(*job)
        done += 1
        if on_progress:
            on_progress(done)

    await asyncio.gather(*(one(j) for j in jobs))


def generate_clips(jobs, on_progress=None):
    asyncio.run(_generate_all(jobs, on_progress))

def generate_beep_wav(out_path, frequency=880, duration_ms=400, sample_rate=24000):
    num_samples = int(sample_rate * duration_ms / 1000)
    amplitude = 16000
    with open(out_path, 'wb') as f:
        data_size = num_samples * 2
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + data_size))
        f.write(b'WAVE')
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))
        f.write(struct.pack('<H', 1))
        f.write(struct.pack('<H', 1))
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', sample_rate * 2))
        f.write(struct.pack('<H', 2))
        f.write(struct.pack('<H', 16))
        f.write(b'data')
        f.write(struct.pack('<I', data_size))
        fade_samples = int(sample_rate * 0.01)
        for i in range(num_samples):
            sample = amplitude * math.sin(2 * math.pi * frequency * i / sample_rate)
            if i < fade_samples:
                sample *= i / fade_samples
            elif i > num_samples - fade_samples:
                sample *= (num_samples - i) / fade_samples
            f.write(struct.pack('<h', int(sample)))

def _require_ffmpeg():
    if not FFMPEG:
        raise RuntimeError(
            "ffmpeg is not available, so audio segments can't be joined. "
            "Single Voice mode still works."
        )


def run_ffmpeg(args, doing):
    """Run ffmpeg and raise with its own error text if it fails.

    Output is captured, so without this check a failed run would leave a
    missing or truncated file that the caller would happily read as audio.
    """
    _require_ffmpeg()
    r = subprocess.run([FFMPEG, "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        tail = " / ".join((r.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(f"ffmpeg failed while {doing}: {tail or 'no error output'}")


def generate_silence_mp3(out_path, duration_ms):
    run_ffmpeg([
        "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_ms / 1000),
        "-q:a", "9", "-acodec", "libmp3lame",
        out_path
    ], "creating a pause")


def stitch_clips(all_segments, out_path):
    _require_ffmpeg()
    fd, tmp_list = tempfile.mkstemp(suffix=".txt", dir=os.path.dirname(out_path) or None)
    try:
        with os.fdopen(fd, "w") as f:
            for seg in all_segments:
                f.write(f"file '{seg}'\n")
        run_ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", tmp_list,
            "-acodec", "libmp3lame", "-q:a", "4", out_path
        ], "joining the audio segments")
    finally:
        try:
            os.unlink(tmp_list)
        except OSError:
            pass


# ── Pause markers ─────────────────────────────────────────────────────────────
# "[3s]" (or "[2.5s]") anywhere in a line inserts a pause at that point. At the
# end of a dialogue line it replaces the normal gap between turns. Scripts with
# no markers behave exactly as before.
PAUSE_MARKER = re.compile(r'\[\s*(\d+(?:\.\d+)?)\s*s\s*\]', re.IGNORECASE)
_LEADING_PUNCT = " ,;:"


def parse_inline_pauses(text):
    """Split text on [Ns] markers into (chunk, pause_after_ms) pairs.

    A chunk can be "" when the text starts with a marker (pause before speech).
    Text with no markers comes back as a single (text, 0) pair.
    """
    parts, last = [], 0
    for m in PAUSE_MARKER.finditer(text):
        chunk = text[last:m.start()].strip().lstrip(_LEADING_PUNCT).strip()
        ms = int(float(m.group(1)) * 1000)
        if chunk or not parts:
            parts.append((chunk, ms))
        else:  # back-to-back markers: merge the pauses
            parts[-1] = (parts[-1][0], parts[-1][1] + ms)
        last = m.end()
    rest = text[last:].strip().lstrip(_LEADING_PUNCT).strip()
    if rest:
        parts.append((rest, 0))
    return parts


def strip_pause_markers(text):
    return re.sub(r"[ \t]{2,}", " ", PAUSE_MARKER.sub(" ", text)).strip()


def split_passage(text):
    """Single-voice passage -> (chunk, pause_after_ms) pairs.

    Unmarked lines are joined into one chunk, so a passage with no markers is
    still synthesised as a single clip with natural prosody.
    """
    chunks, buf = [], []

    def flush(pause):
        if buf:
            chunks.append(("\n".join(buf), pause))
            buf.clear()
        elif pause:
            chunks.append(("", pause))

    for line in text.strip().splitlines():
        for chunk, pause in parse_inline_pauses(line):
            if chunk:
                buf.append(chunk)
            if pause:
                flush(pause)
    flush(0)
    return chunks


_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "e.g.", "i.e.",
                  "etc.", "vs.", "no.", "approx."}


def split_sentences(text):
    """Dictation units: each line, further split into sentences."""
    sentences = []
    for line in text.strip().splitlines():
        line = strip_pause_markers(line)
        if not line:
            continue
        for s in re.split(r'(?<=[.!?])\s+(?=["\'(A-Z0-9])', line):
            s = s.strip()
            if not s:
                continue
            # "Mr. Smith" must not be split into two dictation sentences
            if sentences and sentences[-1].split()[-1].lower() in _ABBREVIATIONS:
                sentences[-1] += " " + s
            else:
                sentences.append(s)
    return sentences


# ── Speakers ──────────────────────────────────────────────────────────────────
# Any short name works as a tag: "A:", "Emma:", "Dr. Patel:", "Shop Assistant:".
SPEAKER_LINE = re.compile(r"^\s*([A-Za-z][\w .'\-()]{0,24}?)\s*:\s*(.*)$")
_NOT_SPEAKERS = {"http", "https", "note", "notes", "ps", "answer", "answers"}
MAX_NAME_WORDS = 3


def parse_dialogue(text):
    """Return (speakers in order of appearance, [(speaker, chunks), ...]).

    Untagged lines continue the previous speaker's turn.
    """
    speakers, canonical, lines = [], {}, []
    for raw in text.strip().splitlines():
        if not raw.strip():
            continue
        m = SPEAKER_LINE.match(raw)
        name = " ".join(m.group(1).split()) if m else ""
        if m and name.lower() in _NOT_SPEAKERS:
            continue  # "Note: ..." or a URL — never read aloud
        is_tag = m and len(name.split()) <= MAX_NAME_WORDS
        if is_tag:
            name = canonical.setdefault(name.lower(), name)  # "doctor" == "Doctor"
            chunks = parse_inline_pauses(m.group(2))
            if not chunks:
                continue
            if name not in speakers:
                speakers.append(name)
            lines.append((name, chunks))
        elif lines:
            chunks = parse_inline_pauses(raw)
            prev = lines[-1][1]
            if chunks and prev[-1][0] and prev[-1][1] == 0 and chunks[0][0]:
                prev[-1] = (prev[-1][0] + " " + chunks[0][0], chunks[0][1])
                chunks = chunks[1:]
            prev.extend(chunks)
    return speakers, lines


def safe_key(name):
    return re.sub(r"\W+", "_", name.lower()).strip("_") or "spk"


# ── Effects & ambience ────────────────────────────────────────────────────────
AUDIO_EFFECTS = {
    "None": None,
    "📞 Telephone": "highpass=f=300,lowpass=f=3400,volume=1.3",
    "📻 Radio / PA speaker": "highpass=f=400,lowpass=f=4000,volume=1.2",
    "🏛️ Large room / hall": "aecho=0.8:0.88:40:0.3",
}

# (noise colour, base amplitude, extra filters). These are synthesised noise
# beds, not recordings — for real chatter or airport chimes, upload a file.
AMBIENCE_OPTIONS = {
    "None": None,
    "🏫 Classroom room tone": ("brown", 0.05, "lowpass=f=500"),
    "☕ Busy room (soft noise)": ("pink", 0.02, "lowpass=f=1200"),
    "🌧️ Rain outside": ("pink", 0.035, "bandpass=f=1400:t=h:w=1600"),
    "✈️ Terminal / hall hum": ("brown", 0.06, "lowpass=f=250,aecho=0.8:0.9:60:0.4"),
    "📼 Old tape hiss": ("white", 0.006, "highpass=f=3000"),
    "📁 Upload my own audio…": "upload",
}
AMBIENCE_DEFAULT_LEVEL = 4


def apply_effect_inplace(path, audio_filter):
    tmp = path[:-4] + "_fx.mp3"
    run_ffmpeg([
        "-i", path, "-af", audio_filter,
        "-ar", "24000", "-ac", "1", "-acodec", "libmp3lame", "-q:a", "4", tmp
    ], "applying an audio effect")
    os.replace(tmp, path)


def mix_ambience(speech_path, out_path, choice, level, upload_path=None):
    """Lay a looping background bed under speech_path, ending with the speech."""
    factor = level / AMBIENCE_DEFAULT_LEVEL
    if upload_path:
        inputs = ["-i", speech_path, "-stream_loop", "-1", "-i", upload_path]
        bed = (f"[1:a]aresample=24000,aformat=channel_layouts=mono,"
               f"volume={0.08 * factor:.3f},afade=t=in:d=1.5[amb]")
    else:
        colour, amp, extra = AMBIENCE_OPTIONS[choice]
        inputs = ["-i", speech_path]
        bed = (f"anoisesrc=color={colour}:r=24000:a={amp * factor:.4f},{extra},"
               f"afade=t=in:d=1.5[amb]")
    run_ffmpeg([
        *inputs,
        "-filter_complex",
        f"{bed};[0:a][amb]amix=inputs=2:duration=first:normalize=0[out]",
        "-map", "[out]", "-ar", "24000", "-ac", "1",
        "-acodec", "libmp3lame", "-q:a", "4", out_path
    ], "adding background ambience")


def make_beep(tmp_dir):
    beep_wav = os.path.join(tmp_dir, "beep.wav")
    beep_mp3 = os.path.join(tmp_dir, "beep.mp3")
    generate_beep_wav(beep_wav)
    run_ffmpeg(["-i", beep_wav, "-acodec", "libmp3lame", "-q:a", "4", beep_mp3],
               "encoding the beep")
    return beep_mp3


def silence_cache(tmp_dir):
    """Silence clips are reused across a build, so each duration is made once."""
    made = {}

    def get(ms):
        ms = int(ms)
        if ms not in made:
            path = os.path.join(tmp_dir, f"silence_{ms}.mp3")
            generate_silence_mp3(path, ms)
            made[ms] = path
        return made[ms]

    return get


PREVIEW_TEXT = "The train to the city centre leaves at half past nine."


def voice_preview(voice_id, key, rate=90, pitch=0, container=None):
    """A one-line sample of a voice, so picking between 30 of them is quick."""
    container = container or st.sidebar
    state_key = f"_preview_{key}"
    if container.button("🔊 Preview voice", key=f"_prevbtn_{key}", width="stretch"):
        d = tempfile.mkdtemp(prefix="lfc_prev_")
        try:
            path = os.path.join(d, "preview.mp3")
            generate_clips([(PREVIEW_TEXT, voice_id, rate, pitch, path)])
            with open(path, "rb") as f:
                st.session_state[state_key] = ((voice_id, rate, pitch), f.read())
        except Exception as e:
            container.error(f"Preview failed: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    cached = st.session_state.get(state_key)
    # Drop the player if the settings moved on, so it can't play the wrong thing
    if cached and cached[0] == (voice_id, rate, pitch):
        container.audio(cached[1], format="audio/mp3")


# ── Presets ───────────────────────────────────────────────────────────────────
# Preset-controlled widgets read their value from session state (no value=
# argument), so a preset can rewrite them all in one click.
DEFAULTS = {
    "mode": "Single Voice",
    "use_announcement": True,
    "announcement_text": "You are going to hear a recording. Listen carefully.",
    "reading_time_s": 0,
    "reading_text": "You now have {n} seconds to look at the questions.",
    "use_beep": True,
    "pause_after_intro_ms": 1500,
    "play_twice": False,
    "repeat_gap_ms": 3000,
    "repeat_lead_in": "Now listen again.",
    "closing_text": "",
    "dictation": False,
    "dictation_gap_s": 5.0,
    "dictation_reads": 2,
}
for _k, _v in DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

PRESETS = {
    "🎓 Cambridge / IELTS part": {
        "use_announcement": True,
        "announcement_text": ("Part 1. You will hear a conversation between two people. "
                              "For questions 1 to 5, choose the correct answer."),
        "reading_time_s": 30,
        "reading_text": "You now have {n} seconds to look at questions 1 to 5.",
        "use_beep": True,
        "pause_after_intro_ms": 1500,
        "play_twice": True,
        "repeat_gap_ms": 5000,
        "repeat_lead_in": "Now listen again.",
        "closing_text": "That is the end of Part 1.",
        "dictation": False,
    },
    "✍️ Dictation": {
        "mode": "Single Voice",
        "use_announcement": True,
        "announcement_text": ("Dictation. Listen and write down each sentence. "
                              "You will hear each sentence twice."),
        "reading_time_s": 0,
        "use_beep": True,
        "pause_after_intro_ms": 1500,
        "play_twice": False,
        "closing_text": "That is the end of the dictation.",
        "dictation": True,
        "dictation_gap_s": 5.0,
        "dictation_reads": 2,
    },
    "↺ Reset to defaults": DEFAULTS,
}


def _apply_preset():
    for k, v in PRESETS[st.session_state["preset"]].items():
        st.session_state[k] = v


# ── Sidebar: core settings ────────────────────────────────────────────────────
st.sidebar.header("Settings")
st.sidebar.selectbox("Exam preset", list(PRESETS), key="preset",
                     help="Fills in the intro, timing and playback settings below.")
st.sidebar.button("Apply preset", on_click=_apply_preset, width="stretch")

mode = st.sidebar.radio("Mode", ["Single Voice", "Dialogue"], key="mode")
filename = st.sidebar.text_input("Output filename", value="listening-activity-1")

if mode == "Single Voice":
    st.sidebar.subheader("Voice")
    voice_label = st.sidebar.selectbox("Voice", voice_list)
    voice_id = VOICES[voice_label]
    rate = st.sidebar.slider("Speed (%)", 50, 120, 90, 5,
                             help="100 = normal. 75–90 recommended for EFL.")
    pitch = st.sidebar.slider("Pitch (Hz)", -20, 20, 0, 1)
    voice_preview(voice_id, "single", rate, pitch)
    single_effect = AUDIO_EFFECTS[st.sidebar.selectbox("Audio effect", list(AUDIO_EFFECTS))]

    st.sidebar.subheader("Dictation")
    dictation = st.sidebar.checkbox(
        "Dictation mode", key="dictation",
        help="Reads each sentence, pauses, reads it again, pauses, then moves on.")
    dictation_gap_s = st.sidebar.slider("Writing time after each reading (s)",
                                        1.0, 20.0, step=0.5, key="dictation_gap_s",
                                        disabled=not dictation)
    dictation_reads = st.sidebar.slider("Times each sentence is read", 1, 3,
                                        key="dictation_reads", disabled=not dictation)
else:
    dictation = False
    st.sidebar.subheader("Dialogue Timing")
    pause_ms = st.sidebar.slider("Pause between turns (ms)", 200, 2000, 600, 100,
                                 help="500–800ms feels natural.")
    st.sidebar.subheader("Speakers")
    speaker_box = st.sidebar.container()  # filled once the script is parsed

# ── Intro settings ────────────────────────────────────────────────────────────
st.sidebar.subheader("Intro")
use_announcement = st.sidebar.checkbox("Add announcement", key="use_announcement")
announcement_voice_label = st.sidebar.selectbox(
    "Announcement voice", list(ANNOUNCEMENT_VOICES.keys()),
    help="Also reads the reading-time instruction, lead-in and closing line."
)
announcement_voice = ANNOUNCEMENT_VOICES[announcement_voice_label]
voice_preview(announcement_voice, "announcement")
announcement_text = st.sidebar.text_area(
    "Announcement text", height=80, key="announcement_text",
    disabled=not use_announcement
)
reading_time_s = st.sidebar.slider(
    "Reading time before audio (s)", 0, 60, step=5, key="reading_time_s",
    help="Silent time for students to read the questions. 0 = none."
)
reading_text = st.sidebar.text_input(
    "Reading time instruction", key="reading_text",
    disabled=reading_time_s == 0,
    help="{n} is replaced with the number of seconds. Leave blank for silence only."
)
use_beep = st.sidebar.checkbox("Add tone before the recording", key="use_beep")
pause_after_intro_ms = st.sidebar.slider(
    "Pause after intro (ms)", 500, 4000, step=500, key="pause_after_intro_ms",
    help="Gap between the intro and the main audio."
)

# ── Playback ──────────────────────────────────────────────────────────────────
st.sidebar.subheader("Playback")
play_twice = st.sidebar.checkbox(
    "Play passage twice", key="play_twice",
    help="Exam format: the whole passage again after a gap."
)
repeat_gap_ms = st.sidebar.slider(
    "Gap before second play (ms)", 1000, 10000, step=500, key="repeat_gap_ms",
    disabled=not play_twice
)
repeat_lead_in = st.sidebar.text_input(
    "Lead-in to second play", key="repeat_lead_in", disabled=not play_twice,
    help="Spoken in the announcement voice. Leave blank for a silent gap."
)
closing_text = st.sidebar.text_input(
    "Closing announcement", key="closing_text",
    help='e.g. "That is the end of Part 1." Leave blank for none.'
)

# ── Atmosphere ────────────────────────────────────────────────────────────────
st.sidebar.subheader("Background ambience")
ambience_choice = st.sidebar.selectbox(
    "Ambience", list(AMBIENCE_OPTIONS),
    help="A quiet background bed under the passage (not under announcements)."
)
ambience_level = st.sidebar.slider("Ambience level", 1, 10, AMBIENCE_DEFAULT_LEVEL,
                                   disabled=ambience_choice == "None")
ambience_upload = None
if AMBIENCE_OPTIONS[ambience_choice] == "upload":
    ambience_upload = st.sidebar.file_uploader(
        "Background audio (loops)", type=["mp3", "wav", "m4a", "ogg"])

# ── Main area ─────────────────────────────────────────────────────────────────
PAUSE_HELP = ("Pauses: put `[2s]` (or `[1.5s]`) anywhere in a line to insert a pause "
              "right there — great for hesitation, dictation chunks or gap-fill.")
dialogue_speakers, dialogue_lines, speaker_cfg = [], [], {}

if mode == "Single Voice":
    if dictation:
        st.write("Paste your dictation text. Each sentence (or line) is read "
                 f"{dictation_reads}× with {dictation_gap_s:g}s of writing time after each reading.")
    else:
        st.write("Paste your listening passage below and generate a single-voice MP3.")
        st.caption(PAUSE_HELP)
    text_input = st.text_area("Your text",
                              placeholder="Paste your listening passage here...",
                              height=240)
    word_count = len(text_input.split()) if text_input.strip() else 0
    caption = f"{word_count} words · {len(text_input)} characters"
    if dictation and text_input.strip():
        caption += f" · {len(split_sentences(text_input))} dictation sentences"
    st.caption(caption)

else:
    st.write("Write your dialogue below. Start each line with the speaker's name or "
             "role and a colon — `A:`, `Emma:`, `Doctor:`, `Shop Assistant:`…")
    st.code("Interviewer: Good morning. [1s] Please, take a seat.\n"
            "Candidate: Thank you. It's nice to meet you.\n"
            "Interviewer: So, tell me a little about yourself. [3s]\n"
            "Candidate: Well [1s] I studied biology at university.", language=None)
    st.caption(PAUSE_HELP + " A pause at the end of a line replaces the normal gap "
               "between turns. Lines without a name continue the previous speaker.")

    text_input = st.text_area(
        "Your dialogue",
        placeholder="Emma: Hi! Are you new here?\nMark: Yes, I started today.",
        height=280
    )
    dialogue_speakers, dialogue_lines = parse_dialogue(text_input)

    if dialogue_lines:
        counts = {}
        for spk, _ in dialogue_lines:
            counts[spk] = counts.get(spk, 0) + 1
        summary = " · ".join(f"{spk}: {n}" for spk, n in counts.items())
        st.caption(f"{len(dialogue_lines)} lines parsed — {summary}")
    elif text_input.strip():
        st.info("No speaker tags found. Start lines with a name and a colon, e.g. `Emma:`")

    # ── Per-speaker voice, speed, pitch and effect ──
    if not dialogue_speakers:
        speaker_box.caption("Speakers appear here once your script has name tags.")
    for i, spk in enumerate(dialogue_speakers):
        k = safe_key(spk)
        with speaker_box.expander(f"🗣️ {spk}", expanded=i < 2):
            default_id = DEFAULT_SPEAKER_VOICES[i % len(DEFAULT_SPEAKER_VOICES)]
            label = st.selectbox("Voice", voice_list,
                                 index=_voice_ids.index(default_id), key=f"voice_{k}")
            spk_rate = st.slider("Speed (%)", 50, 120, 90, 5, key=f"rate_{k}")
            spk_pitch = st.slider("Pitch (Hz)", -20, 20, 0, 1, key=f"pitch_{k}",
                                  help="Lower for an older/deeper voice, higher for a younger one.")
            spk_fx = st.selectbox("Audio effect", list(AUDIO_EFFECTS), key=f"fx_{k}",
                                  help="e.g. Telephone for the caller in a phone conversation.")
            voice_preview(VOICES[label], f"spk_{k}", spk_rate, spk_pitch, container=st)
        speaker_cfg[spk] = {"voice": VOICES[label], "rate": spk_rate,
                            "pitch": spk_pitch, "fx": AUDIO_EFFECTS[spk_fx]}


# ── Build plan ────────────────────────────────────────────────────────────────
# Each plan_* function appends TTS jobs to `jobs` and returns the segment list.
# All jobs are then synthesised together, in parallel, before stitching.
def plan_intro(tmp_dir, get_silence, jobs):
    segs = []
    if use_announcement and announcement_text.strip():
        p = os.path.join(tmp_dir, "announcement.mp3")
        jobs.append((announcement_text.strip(), announcement_voice, 90, 0, p))
        segs += [p, get_silence(600)]
    if reading_time_s > 0:
        txt = reading_text.replace("{n}", str(reading_time_s)).strip()
        if txt:
            p = os.path.join(tmp_dir, "reading_time.mp3")
            jobs.append((txt, announcement_voice, 90, 0, p))
            segs.append(p)
        segs.append(get_silence(reading_time_s * 1000))
    if use_beep:
        segs.append(make_beep(tmp_dir))
    if segs:
        segs.append(get_silence(pause_after_intro_ms))
    return segs


def plan_extras(tmp_dir, jobs):
    """Second-play lead-in and closing line, read in the announcement voice."""
    extras = {"lead_in": None, "closing": None}
    if play_twice and repeat_lead_in.strip():
        extras["lead_in"] = os.path.join(tmp_dir, "repeat_lead_in.mp3")
        jobs.append((repeat_lead_in.strip(), announcement_voice, 90, 0, extras["lead_in"]))
    if closing_text.strip():
        extras["closing"] = os.path.join(tmp_dir, "closing.mp3")
        jobs.append((closing_text.strip(), announcement_voice, 90, 0, extras["closing"]))
    return extras


def assemble_outro(body, extras, get_silence):
    segs = []
    if play_twice:
        segs.append(get_silence(repeat_gap_ms))
        if extras["lead_in"]:
            segs += [extras["lead_in"], get_silence(1000)]
        segs.extend(body)
    if extras["closing"]:
        segs += [get_silence(1500), extras["closing"]]
    return segs


def plan_single(tmp_dir, get_silence, jobs, fx):
    body = []

    def clip(name, text):
        p = os.path.join(tmp_dir, name)
        jobs.append((text, voice_id, rate, pitch, p))
        if single_effect:
            fx[p] = single_effect
        return p

    if dictation:
        gap = get_silence(int(dictation_gap_s * 1000))
        for i, sentence in enumerate(split_sentences(text_input)):
            p = clip(f"sentence_{i}.mp3", sentence)
            body += [p, gap] * dictation_reads
        return body

    for i, (chunk, pause) in enumerate(split_passage(text_input)):
        if chunk:
            body.append(clip(f"part_{i}.mp3", chunk))
        if pause:
            body.append(get_silence(pause))
    return body


def plan_dialogue(tmp_dir, get_silence, jobs, fx):
    body, n = [], 0
    for li, (spk, chunks) in enumerate(dialogue_lines):
        cfg = speaker_cfg[spk]
        for chunk, pause in chunks:
            if chunk:
                p = os.path.join(tmp_dir, f"line_{n}.mp3")
                n += 1
                jobs.append((chunk, cfg["voice"], cfg["rate"], cfg["pitch"], p))
                if cfg["fx"]:
                    fx[p] = cfg["fx"]
                body.append(p)
            if pause:
                body.append(get_silence(pause))
        # A trailing [Ns] marker already supplied this line's gap
        if li < len(dialogue_lines) - 1 and chunks[-1][1] == 0:
            body.append(get_silence(pause_ms))
    return body


def build_audio(tmp_dir, progress):
    get_silence = silence_cache(tmp_dir)
    jobs, fx = [], {}
    intro = plan_intro(tmp_dir, get_silence, jobs)
    extras = plan_extras(tmp_dir, jobs)
    if mode == "Single Voice":
        body = plan_single(tmp_dir, get_silence, jobs, fx)
    else:
        body = plan_dialogue(tmp_dir, get_silence, jobs, fx)
    if not body:
        raise ValueError("There's nothing to read — check your text.")

    total = len(jobs)
    generate_clips(jobs, on_progress=lambda n: progress.progress(
        n / total, text=f"Generating voices… {n} of {total}"))

    if fx:
        progress.progress(1.0, text="Applying audio effects…")
        for path, audio_filter in fx.items():
            apply_effect_inplace(path, audio_filter)

    if AMBIENCE_OPTIONS[ambience_choice] is not None:
        upload_path = None
        if AMBIENCE_OPTIONS[ambience_choice] == "upload":
            if ambience_upload is None:
                raise ValueError("Upload a background audio file, or pick a different ambience.")
            ext = os.path.splitext(ambience_upload.name)[1] or ".mp3"
            upload_path = os.path.join(tmp_dir, f"ambience_upload{ext}")
            with open(upload_path, "wb") as f:
                f.write(ambience_upload.getvalue())
        progress.progress(1.0, text="Adding background ambience…")
        raw = os.path.join(tmp_dir, "body_raw.mp3")
        mixed = os.path.join(tmp_dir, "body_ambience.mp3")
        stitch_clips(body, raw)
        mix_ambience(raw, mixed, ambience_choice, ambience_level, upload_path)
        body = [mixed]

    all_segments = intro + body + assemble_outro(body, extras, get_silence)
    progress.progress(1.0, text="Joining everything together…")
    out_path = os.path.join(tmp_dir, "output.mp3")
    if len(all_segments) == 1:
        out_path = all_segments[0]
    else:
        stitch_clips(all_segments, out_path)
    with open(out_path, "rb") as f:
        return f.read()


def show_transcript():
    with st.expander("📄 View transcript"):
        if use_announcement and announcement_text.strip():
            st.markdown(f"*📢 {announcement_text.strip()}*")
        if reading_time_s > 0 and reading_text.strip():
            st.markdown(f"*📢 {reading_text.replace('{n}', str(reading_time_s))}*")
        st.markdown("---")
        if mode == "Dialogue":
            for spk, chunks in dialogue_lines:
                text = " … ".join(c for c, _ in chunks if c)
                st.markdown(f"**{spk}:** {text}")
        elif dictation:
            for i, s in enumerate(split_sentences(text_input), 1):
                st.markdown(f"{i}. {s}")
        else:
            st.markdown(strip_pause_markers(text_input).replace("\n", "  \n"))
        if closing_text.strip():
            st.markdown("---")
            st.markdown(f"*📢 {closing_text.strip()}*")


# ── Generate ──────────────────────────────────────────────────────────────────
if st.button("⏺ Generate MP3", type="primary", width="stretch"):
    if not text_input.strip():
        st.warning("Please enter some text first.")
    elif mode == "Dialogue" and not dialogue_lines:
        st.warning("No speaker tags found. Start lines with a name and a colon, e.g. `Emma:`")
    else:
        with st.spinner("Generating audio..."):
            tmp_dir = tempfile.mkdtemp(prefix="lfc_")
            progress = st.progress(0, text="Preparing…")
            try:
                audio_bytes = build_audio(tmp_dir, progress)
                progress.empty()
                st.audio(audio_bytes, format="audio/mp3")
                default_name = "dialogue" if mode == "Dialogue" else "listening-audio"
                st.download_button(
                    "⬇ Download MP3", data=audio_bytes,
                    file_name=f"{filename.strip() or default_name}.mp3",
                    mime="audio/mp3", width="stretch"
                )
                if mode == "Dialogue":
                    st.success(f"Done! {len(dialogue_lines)}-line dialogue generated.")
                else:
                    st.success("Done!")
                show_transcript()
            except Exception as e:
                progress.empty()
                st.error(f"Something went wrong: {e}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

st.divider()
st.caption("Built with Streamlit + Microsoft Edge TTS · Single voice, dialogue, dictation & exam presets")
