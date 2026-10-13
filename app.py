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
# A trailing "[3s]" (or "[2.5s]") on a line sets the pause that follows it,
# overriding the global gap. Scripts with no markers behave exactly as before.
PAUSE_MARKER = re.compile(r'\[\s*(\d+(?:\.\d+)?)\s*s\s*\]\s*$', re.IGNORECASE)


def strip_pause_marker(text):
    """Return (text without a trailing [Ns] marker, that pause in ms or None)."""
    m = PAUSE_MARKER.search(text)
    if not m:
        return text.strip(), None
    return PAUSE_MARKER.sub("", text).strip(), int(float(m.group(1)) * 1000)


def split_passage(text):
    """Split a single-voice passage into (chunk, pause_after_ms) pairs.

    With no markers this returns one chunk and no pause — i.e. the single-clip
    behaviour the app had before markers existed.
    """
    chunks, buf = [], []
    for line in text.strip().splitlines():
        body, pause = strip_pause_marker(line)
        if body:
            buf.append(body)
        if pause is not None and buf:
            chunks.append(("\n".join(buf), pause))
            buf = []
    if buf:
        chunks.append(("\n".join(buf), None))
    return chunks


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


def voice_preview(voice_id, key):
    """A one-line sample of a voice, so picking between 30 of them is quick."""
    state_key = f"_preview_{key}"
    if st.sidebar.button("🔊 Preview voice", key=f"_prevbtn_{key}",
                         use_container_width=True):
        d = tempfile.mkdtemp(prefix="lfc_prev_")
        try:
            path = os.path.join(d, "preview.mp3")
            generate_clips([(PREVIEW_TEXT, voice_id, 90, 0, path)])
            with open(path, "rb") as f:
                st.session_state[state_key] = (voice_id, f.read())
        except Exception as e:
            st.sidebar.error(f"Preview failed: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    cached = st.session_state.get(state_key)
    # Drop the player if the selection moved on, so it can't play the wrong voice
    if cached and cached[0] == voice_id:
        st.sidebar.audio(cached[1], format="audio/mp3")


# ── Sidebar: core settings ────────────────────────────────────────────────────
st.sidebar.header("Settings")
mode = st.sidebar.radio("Mode", ["Single Voice", "Dialogue"])
filename = st.sidebar.text_input("Output filename", value="listening-activity-1")

if mode == "Single Voice":
    st.sidebar.subheader("Voice")
    voice_label = st.sidebar.selectbox("Voice", voice_list)
    voice_id = VOICES[voice_label]
    voice_preview(voice_id, "single")
    rate = st.sidebar.slider("Speed (%)", 50, 120, 90, 5,
                              help="100 = normal. 75–90 recommended for EFL.")
    pitch = st.sidebar.slider("Pitch (Hz)", -10, 10, 0, 1)

else:
    st.sidebar.subheader("Dialogue Timing")
    pause_ms = st.sidebar.slider("Pause between turns (ms)", 200, 2000, 600, 100,
                                  help="500–800ms feels natural.")
    rate_global = st.sidebar.slider("Speed — all speakers (%)", 50, 120, 90, 5)
    pitch_global = st.sidebar.slider("Pitch — all speakers (Hz)", -10, 10, 0, 1)

# ── Intro settings ────────────────────────────────────────────────────────────
st.sidebar.subheader("Intro")
use_announcement = st.sidebar.checkbox("Add announcement", value=True)
announcement_voice_label = st.sidebar.selectbox(
    "Announcement voice", list(ANNOUNCEMENT_VOICES.keys()),
    disabled=not use_announcement
)
if use_announcement:
    voice_preview(ANNOUNCEMENT_VOICES[announcement_voice_label], "announcement")
announcement_text = st.sidebar.text_area(
    "Announcement text",
    value="You are going to hear a recording. Listen carefully.",
    height=80,
    disabled=not use_announcement
)
use_beep = st.sidebar.checkbox("Add beep after announcement", value=True)
pause_after_intro_ms = st.sidebar.slider(
    "Pause after intro (ms)", 500, 4000, 1500, 500,
    help="Gap between the intro and the main audio."
)

# ── Playback ──────────────────────────────────────────────────────────────────
st.sidebar.subheader("Playback")
play_twice = st.sidebar.checkbox(
    "Play passage twice", value=False,
    help="Exam format: the whole passage again after a gap."
)
repeat_gap_ms = st.sidebar.slider(
    "Gap before second play (ms)", 1000, 10000, 3000, 500,
    disabled=not play_twice
)
repeat_lead_in = st.sidebar.text_input(
    "Lead-in to second play", value="Now listen again.",
    disabled=not play_twice,
    help="Spoken in the announcement voice. Leave blank for a silent gap."
)

# ── Main area ─────────────────────────────────────────────────────────────────
if mode == "Single Voice":
    st.write("Paste your listening passage below and generate a single-voice MP3.")
    st.caption("Pause control: end any line with `[3s]` to put a 3-second gap after it — handy for dictation and gap-fill.")
    text_input = st.text_area("Your text",
                               placeholder="Paste your listening passage here...",
                               height=240)
    word_count = len(text_input.split()) if text_input.strip() else 0
    st.caption(f"{word_count} words · {len(text_input)} characters")
    detected_speakers = []
    speaker_voices = {}

else:
    st.write("Write your dialogue below. Tag each line with a speaker letter: `A:`, `B:`, `C:`, etc.")
    st.code("A: Good morning everyone.\nB: Hello! Nice to meet you. [3s]\nC: Hi! Are we starting now?\nA: Yes, let's begin.", language=None)
    st.caption("Pause control: end any line with `[3s]` to put a 3-second gap after it — handy for dictation and gap-fill.")

    text_input = st.text_area(
        "Your dialogue",
        placeholder="A: Good morning!\nB: Hi, how are you?\nC: I'm great, thanks!\nA: Let's get started...",
        height=280
    )

    # Detect speakers from current text
    detected_speakers = []
    for line in text_input.strip().splitlines():
        m = re.match(r'^([A-Z])\s*:\s*', line.strip())
        if m and m.group(1) not in detected_speakers:
            detected_speakers.append(m.group(1))
    detected_speakers.sort()

    # Line count preview
    if detected_speakers:
        lines = [l.strip() for l in text_input.strip().splitlines() if l.strip()]
        parsed_preview = []
        for line in lines:
            m = re.match(r'^([A-Z])\s*:\s*(.+)', line)
            if m:
                parsed_preview.append((m.group(1), m.group(2).strip()))
        if parsed_preview:
            counts = {}
            for spk, _ in parsed_preview:
                counts[spk] = counts.get(spk, 0) + 1
            summary = " · ".join(f"{spk}: {n} lines" for spk, n in sorted(counts.items()))
            st.caption(f"{len(parsed_preview)} lines parsed — {summary}")
    elif text_input.strip():
        st.info("No speaker tags found. Start lines with A:, B:, C: etc.")

    # ── Speaker voice selectors — always in sidebar so values persist ──
    speaker_voices = {}
    if detected_speakers:
        st.sidebar.subheader("Speaker Voices")
        for i, spk in enumerate(detected_speakers):
            default_id = DEFAULT_SPEAKER_VOICES[i % len(DEFAULT_SPEAKER_VOICES)]
            default_idx = _voice_ids.index(default_id)
            label = st.sidebar.selectbox(
                f"Speaker {spk}",
                voice_list,
                index=default_idx,
                key=f"voice_{spk}"
            )
            speaker_voices[spk] = VOICES[label]
            voice_preview(speaker_voices[spk], f"spk_{spk}")
    else:
        # Still need to read any previously set sidebar widgets so state is consistent
        for spk in "ABCDEF":
            key = f"voice_{spk}"
            if key in st.session_state:
                speaker_voices[spk] = VOICES[st.session_state[key]]

# ── Intro builder ─────────────────────────────────────────────────────────────
def build_intro_segments(tmp_dir):
    segments = []
    if use_announcement and announcement_text.strip():
        ann_path = os.path.join(tmp_dir, "announcement.mp3")
        asyncio.run(generate_clip(
            announcement_text.strip(),
            ANNOUNCEMENT_VOICES[announcement_voice_label],
            90, 0, ann_path
        ))
        segments.append(ann_path)
        ann_pause_path = os.path.join(tmp_dir, "ann_pause.mp3")
        generate_silence_mp3(ann_pause_path, 600)
        segments.append(ann_pause_path)

    if use_beep:
        beep_wav = os.path.join(tmp_dir, "beep.wav")
        beep_mp3 = os.path.join(tmp_dir, "beep.mp3")
        generate_beep_wav(beep_wav)
        run_ffmpeg([
            "-i", beep_wav,
            "-acodec", "libmp3lame", "-q:a", "4", beep_mp3
        ], "encoding the beep")
        segments.append(beep_mp3)

    if segments:
        pause_path = os.path.join(tmp_dir, "intro_pause.mp3")
        generate_silence_mp3(pause_path, pause_after_intro_ms)
        segments.append(pause_path)

    return segments


def build_repeat_segments(tmp_dir, body_segments, get_silence):
    """Gap, optional spoken lead-in, then the whole passage again."""
    segs = [get_silence(repeat_gap_ms)]
    if repeat_lead_in.strip():
        lead_path = os.path.join(tmp_dir, "repeat_lead_in.mp3")
        generate_clips([(
            repeat_lead_in.strip(),
            ANNOUNCEMENT_VOICES[announcement_voice_label],
            90, 0, lead_path
        )])
        segs.append(lead_path)
        segs.append(get_silence(1000))
    segs.extend(body_segments)
    return segs

# ── Generate ──────────────────────────────────────────────────────────────────
if st.button("⏺ Generate MP3", type="primary", use_container_width=True):
    if not text_input.strip():
        st.warning("Please enter some text first.")

    elif mode == "Single Voice":
        with st.spinner("Generating audio..."):
            tmp_dir = tempfile.mkdtemp(prefix="lfc_")
            try:
                get_silence = silence_cache(tmp_dir)
                all_segments = build_intro_segments(tmp_dir)

                chunks = split_passage(text_input)
                jobs, body = [], []
                for i, (chunk, pause) in enumerate(chunks):
                    part_path = os.path.join(tmp_dir, f"part_{i}.mp3")
                    jobs.append((chunk, voice_id, rate, pitch, part_path))
                    body.append(part_path)
                    if pause:
                        body.append(get_silence(pause))

                progress = st.progress(0, text="Generating audio...")
                generate_clips(jobs, on_progress=lambda n: progress.progress(
                    n / len(jobs), text=f"{n} of {len(jobs)} parts..."
                ))

                all_segments.extend(body)
                if play_twice:
                    all_segments.extend(build_repeat_segments(tmp_dir, body, get_silence))

                out_path = os.path.join(tmp_dir, "output.mp3")
                if len(all_segments) == 1:
                    out_path = all_segments[0]
                else:
                    stitch_clips(all_segments, out_path)
                with open(out_path, "rb") as f:
                    audio_bytes = f.read()
                st.audio(audio_bytes, format="audio/mp3")
                st.download_button(
                    "⬇ Download MP3", data=audio_bytes,
                    file_name=f"{filename.strip() or 'listening-audio'}.mp3",
                    mime="audio/mp3", use_container_width=True
                )
                st.success("Done!")
            except Exception as e:
                st.error(f"Something went wrong: {e}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    else:  # Dialogue mode
        if not detected_speakers:
            st.warning("No speaker tags found. Start lines with A:, B:, C: etc.")
        elif not speaker_voices:
            st.warning("Speaker voices not set. Please add your script first so voices appear in the sidebar.")
        else:
            lines = [l.strip() for l in text_input.strip().splitlines() if l.strip()]
            parsed = []
            for line in lines:
                m = re.match(r'^([A-Z])\s*:\s*(.+)', line)
                if m and m.group(1) in speaker_voices:
                    body_text, pause_override = strip_pause_marker(m.group(2))
                    if body_text:
                        parsed.append((m.group(1), body_text, pause_override))

            if not parsed:
                st.warning("No valid tagged lines found. Check your format.")
            else:
                with st.spinner(f"Generating {len(parsed)} lines of dialogue..."):
                    tmp_dir = tempfile.mkdtemp(prefix="lfc_")
                    try:
                        get_silence = silence_cache(tmp_dir)
                        all_segments = build_intro_segments(tmp_dir)

                        progress = st.progress(0, text="Generating voices...")
                        dialogue_clips = [
                            os.path.join(tmp_dir, f"line_{i}.mp3")
                            for i in range(len(parsed))
                        ]
                        jobs = [
                            (text, speaker_voices[spk], rate_global, pitch_global, path)
                            for (spk, text, _), path in zip(parsed, dialogue_clips)
                        ]
                        generate_clips(jobs, on_progress=lambda n: progress.progress(
                            n / len(jobs), text=f"{n} of {len(jobs)} lines..."
                        ))

                        body = []
                        for i, clip in enumerate(dialogue_clips):
                            body.append(clip)
                            if i < len(dialogue_clips) - 1:
                                override = parsed[i][2]
                                body.append(get_silence(override or pause_ms))
                        all_segments.extend(body)
                        if play_twice:
                            all_segments.extend(
                                build_repeat_segments(tmp_dir, body, get_silence)
                            )

                        out_path = os.path.join(tmp_dir, "output.mp3")
                        stitch_clips(all_segments, out_path)

                        with open(out_path, "rb") as f:
                            audio_bytes = f.read()

                        st.audio(audio_bytes, format="audio/mp3")
                        st.download_button(
                            "⬇ Download MP3", data=audio_bytes,
                            file_name=f"{filename.strip() or 'dialogue'}.mp3",
                            mime="audio/mp3", use_container_width=True
                        )
                        st.success(f"Done! {len(parsed)}-line dialogue generated.")

                        with st.expander("📄 View transcript"):
                            if use_announcement and announcement_text.strip():
                                st.markdown(f"*📢 {announcement_text.strip()}*")
                                st.markdown("---")
                            for spk, text, _ in parsed:
                                voice_name = [k for k, v in VOICES.items() if v == speaker_voices[spk]]
                                name = voice_name[0].split("(")[1].split(")")[0] if voice_name else spk
                                st.markdown(f"**{spk} — {name}:** {text}")

                    except Exception as e:
                        st.error(f"Something went wrong: {e}")
                    finally:
                        shutil.rmtree(tmp_dir, ignore_errors=True)

st.divider()
st.caption("Built with Streamlit + Microsoft Edge TTS · Single voice & multi-speaker dialogue")
