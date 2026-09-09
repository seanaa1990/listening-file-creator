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


def generate_silence_mp3(out_path, duration_ms):
    _require_ffmpeg()
    subprocess.run([
        FFMPEG, "-y", "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_ms / 1000),
        "-q:a", "9", "-acodec", "libmp3lame",
        out_path
    ], capture_output=True)

def stitch_clips(all_segments, out_path):
    _require_ffmpeg()
    tmp_list = tempfile.mktemp(suffix=".txt")
    with open(tmp_list, "w") as f:
        for seg in all_segments:
            f.write(f"file '{seg}'\n")
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0",
        "-i", tmp_list,
        "-acodec", "libmp3lame", "-q:a", "4", out_path
    ], capture_output=True)
    os.unlink(tmp_list)

# ── Sidebar: core settings ────────────────────────────────────────────────────
st.sidebar.header("Settings")
mode = st.sidebar.radio("Mode", ["Single Voice", "Dialogue"])
filename = st.sidebar.text_input("Output filename", value="listening-activity-1")

if mode == "Single Voice":
    st.sidebar.subheader("Voice")
    voice_label = st.sidebar.selectbox("Voice", voice_list)
    voice_id = VOICES[voice_label]
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

# ── Main area ─────────────────────────────────────────────────────────────────
if mode == "Single Voice":
    st.write("Paste your listening passage below and generate a single-voice MP3.")
    text_input = st.text_area("Your text",
                               placeholder="Paste your listening passage here...",
                               height=240)
    word_count = len(text_input.split()) if text_input.strip() else 0
    st.caption(f"{word_count} words · {len(text_input)} characters")
    detected_speakers = []
    speaker_voices = {}

else:
    st.write("Write your dialogue below. Tag each line with a speaker letter: `A:`, `B:`, `C:`, etc.")
    st.code("A: Good morning everyone.\nB: Hello! Nice to meet you.\nC: Hi! Are we starting now?\nA: Yes, let's begin.", language=None)

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
        subprocess.run([
            FFMPEG, "-y", "-i", beep_wav,
            "-acodec", "libmp3lame", "-q:a", "4", beep_mp3
        ], capture_output=True)
        segments.append(beep_mp3)

    if segments:
        pause_path = os.path.join(tmp_dir, "intro_pause.mp3")
        generate_silence_mp3(pause_path, pause_after_intro_ms)
        segments.append(pause_path)

    return segments

# ── Generate ──────────────────────────────────────────────────────────────────
if st.button("⏺ Generate MP3", type="primary", use_container_width=True):
    if not text_input.strip():
        st.warning("Please enter some text first.")

    elif mode == "Single Voice":
        with st.spinner("Generating audio..."):
            try:
                tmp_dir = tempfile.mkdtemp()
                all_segments = build_intro_segments(tmp_dir)
                main_path = os.path.join(tmp_dir, "main.mp3")
                asyncio.run(generate_clip(text_input.strip(), voice_id, rate, pitch, main_path))
                all_segments.append(main_path)
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
                    parsed.append((m.group(1), m.group(2).strip()))

            if not parsed:
                st.warning("No valid tagged lines found. Check your format.")
            else:
                with st.spinner(f"Generating {len(parsed)} lines of dialogue..."):
                    try:
                        tmp_dir = tempfile.mkdtemp()
                        all_segments = build_intro_segments(tmp_dir)
                        silence_path = os.path.join(tmp_dir, "silence.mp3")
                        generate_silence_mp3(silence_path, pause_ms)

                        progress = st.progress(0, text="Generating voices...")
                        dialogue_clips = []

                        for i, (spk, text) in enumerate(parsed):
                            clip_path = os.path.join(tmp_dir, f"line_{i}.mp3")
                            asyncio.run(generate_clip(
                                text,
                                speaker_voices[spk],
                                rate_global,
                                pitch_global,
                                clip_path
                            ))
                            dialogue_clips.append(clip_path)
                            progress.progress(
                                (i + 1) / len(parsed),
                                text=f"Line {i+1} of {len(parsed)}..."
                            )

                        for i, clip in enumerate(dialogue_clips):
                            all_segments.append(clip)
                            if i < len(dialogue_clips) - 1:
                                all_segments.append(silence_path)

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
                            for spk, text in parsed:
                                voice_name = [k for k, v in VOICES.items() if v == speaker_voices[spk]]
                                name = voice_name[0].split("(")[1].split(")")[0] if voice_name else spk
                                st.markdown(f"**{spk} — {name}:** {text}")

                    except Exception as e:
                        st.error(f"Something went wrong: {e}")

st.divider()
st.caption("Built with Streamlit + Microsoft Edge TTS · Single voice & multi-speaker dialogue")
