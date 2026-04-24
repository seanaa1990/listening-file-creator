import streamlit as st
import edge_tts
import asyncio
import tempfile
import os
import subprocess
import re
import struct
import math

st.set_page_config(page_title="Listening File Creator", page_icon="🎧", layout="centered")

st.title("🎧 Listening File Creator")

VOICES = {
    # British
    "🇬🇧 British Female (Sonia) — clear, neutral": "en-GB-SoniaNeural",
    "🇬🇧 British Female (Maisie) — younger sound": "en-GB-MaisieNeural",
    "🇬🇧 British Male (Ryan) — warm, natural": "en-GB-RyanNeural",
    # American
    "🇺🇸 American Female (Jenny) — friendly": "en-US-JennyNeural",
    "🇺🇸 American Male (Guy) — clear": "en-US-GuyNeural",
    # Australian
    "🇦🇺 Australian Female (Natasha)": "en-AU-NatashaNeural",
    "🇦🇺 Australian Male (William)": "en-AU-WilliamNeural",
    # Canadian
    "🇨🇦 Canadian Female (Clara)": "en-CA-ClaraNeural",
    # Singapore
    "🇸🇬 Singapore Female (Luna)": "en-SG-LunaNeural",
    # Indian
    "🇮🇳 Indian Female (Neerja)": "en-IN-NeerjaNeural",
    "🇮🇳 Indian Male (Prabhat)": "en-IN-PrabhatNeural",
}

ANNOUNCEMENT_VOICES = {
    "🇬🇧 British Female (Sonia)": "en-GB-SoniaNeural",
    "🇬🇧 British Male (Ryan)": "en-GB-RyanNeural",
    "🇺🇸 American Female (Jenny)": "en-US-JennyNeural",
    "🇺🇸 American Male (Guy)": "en-US-GuyNeural",
}

async def generate_clip(text, voice, rate_pct, pitch_hz, out_path):
    rate_str = f"{rate_pct - 100:+d}%"
    pitch_str = f"{pitch_hz:+d}Hz"
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    await communicate.save(out_path)

def generate_beep_wav(out_path, frequency=880, duration_ms=400, sample_rate=24000):
    """Generate a simple sine wave beep as a WAV file."""
    num_samples = int(sample_rate * duration_ms / 1000)
    amplitude = 16000

    with open(out_path, 'wb') as f:
        # WAV header
        data_size = num_samples * 2
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + data_size))
        f.write(b'WAVE')
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))           # chunk size
        f.write(struct.pack('<H', 1))            # PCM
        f.write(struct.pack('<H', 1))            # mono
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', sample_rate * 2))
        f.write(struct.pack('<H', 2))            # block align
        f.write(struct.pack('<H', 16))           # bits per sample
        f.write(b'data')
        f.write(struct.pack('<I', data_size))

        # Fade in/out over 10ms to avoid clicks
        fade_samples = int(sample_rate * 0.01)
        for i in range(num_samples):
            sample = amplitude * math.sin(2 * math.pi * frequency * i / sample_rate)
            if i < fade_samples:
                sample *= i / fade_samples
            elif i > num_samples - fade_samples:
                sample *= (num_samples - i) / fade_samples
            f.write(struct.pack('<h', int(sample)))

def generate_silence_mp3(out_path, duration_ms):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_ms / 1000),
        "-q:a", "9", "-acodec", "libmp3lame",
        out_path
    ], capture_output=True)

def stitch_clips_ffmpeg(clip_paths, pause_ms, output_path):
    tmp_dir = tempfile.mkdtemp()
    silence_path = os.path.join(tmp_dir, "silence.mp3")
    generate_silence_mp3(silence_path, pause_ms)

    all_segments = []
    for i, clip in enumerate(clip_paths):
        all_segments.append(clip)
        if i < len(clip_paths) - 1:
            all_segments.append(silence_path)

    list_path = os.path.join(tmp_dir, "list.txt")
    with open(list_path, "w") as f:
        for seg in all_segments:
            f.write(f"file '{seg}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-acodec", "libmp3lame", "-q:a", "4",
        output_path
    ], capture_output=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.header("Settings")
mode = st.sidebar.radio("Mode", ["Single Voice", "Dialogue (A & B)"])
filename = st.sidebar.text_input("Output filename", value="listening-activity-1")

voice_list = list(VOICES.keys())

if mode == "Single Voice":
    st.sidebar.subheader("Voice")
    voice_label = st.sidebar.selectbox("Voice", voice_list)
    voice_id = VOICES[voice_label]
    rate = st.sidebar.slider("Speed (%)", 50, 120, 90, 5,
                              help="100 = normal. 75–90 recommended for EFL.")
    pitch = st.sidebar.slider("Pitch (Hz)", -10, 10, 0, 1)

else:
    st.sidebar.subheader("Speaker A")
    voice_a_label = st.sidebar.selectbox("Voice A", voice_list, index=0)
    voice_a = VOICES[voice_a_label]
    rate_a = st.sidebar.slider("Speed A (%)", 50, 120, 90, 5)
    pitch_a = st.sidebar.slider("Pitch A (Hz)", -10, 10, 0, 1)

    st.sidebar.subheader("Speaker B")
    voice_b_label = st.sidebar.selectbox("Voice B", voice_list, index=2)
    voice_b = VOICES[voice_b_label]
    rate_b = st.sidebar.slider("Speed B (%)", 50, 120, 90, 5)
    pitch_b = st.sidebar.slider("Pitch B (Hz)", -10, 10, 0, 1)

    st.sidebar.subheader("Dialogue Timing")
    pause_ms = st.sidebar.slider(
        "Pause between turns (ms)", 200, 2000, 600, 100,
        help="Gap between each speaker's line. 500–800ms feels natural."
    )

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

else:
    st.write("Write your dialogue below. Start each line with `A:` or `B:` to assign the speaker.")
    st.code("A: Good morning! Can I help you?\nB: Yes, I'd like a coffee please.\nA: Sure, what size?\nB: Large please.", language=None)
    text_input = st.text_area("Your dialogue",
                               placeholder="A: Good morning!\nB: Hi, how are you?\nA: I'm fine thanks...",
                               height=280)

    if text_input.strip():
        lines = [l.strip() for l in text_input.strip().splitlines() if l.strip()]
        parsed_preview = []
        errors = []
        for i, line in enumerate(lines, 1):
            m = re.match(r'^([AB])\s*:\s*(.+)', line, re.IGNORECASE)
            if m:
                parsed_preview.append((m.group(1).upper(), m.group(2).strip()))
            else:
                errors.append(f"Line {i} ignored (no A: or B: prefix): `{line}`")
        if parsed_preview:
            a_count = sum(1 for s, _ in parsed_preview if s == "A")
            b_count = sum(1 for s, _ in parsed_preview if s == "B")
            st.caption(f"{len(parsed_preview)} lines parsed — {a_count} from A · {b_count} from B")
        for e in errors:
            st.warning(e)

# ── Generate ──────────────────────────────────────────────────────────────────
def build_intro_segments(tmp_dir):
    """Returns list of mp3 file paths for the intro sequence."""
    segments = []

    if use_announcement and announcement_text.strip():
        ann_path = os.path.join(tmp_dir, "announcement.mp3")
        asyncio.run(generate_clip(
            announcement_text.strip(),
            ANNOUNCEMENT_VOICES[announcement_voice_label],
            90, 0, ann_path
        ))
        segments.append(ann_path)

        # Short pause after announcement
        ann_pause_path = os.path.join(tmp_dir, "ann_pause.mp3")
        generate_silence_mp3(ann_pause_path, 600)
        segments.append(ann_pause_path)

    if use_beep:
        beep_wav = os.path.join(tmp_dir, "beep.wav")
        beep_mp3 = os.path.join(tmp_dir, "beep.mp3")
        generate_beep_wav(beep_wav)
        subprocess.run([
            "ffmpeg", "-y", "-i", beep_wav,
            "-acodec", "libmp3lame", "-q:a", "4", beep_mp3
        ], capture_output=True)
        segments.append(beep_mp3)

    if segments:
        pause_path = os.path.join(tmp_dir, "intro_pause.mp3")
        generate_silence_mp3(pause_path, pause_after_intro_ms)
        segments.append(pause_path)

    return segments

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
                    list_path = os.path.join(tmp_dir, "list.txt")
                    with open(list_path, "w") as f:
                        for seg in all_segments:
                            f.write(f"file '{seg}'\n")
                    subprocess.run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", list_path,
                        "-acodec", "libmp3lame", "-q:a", "4", out_path
                    ], capture_output=True)

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

    else:
        lines = [l.strip() for l in text_input.strip().splitlines() if l.strip()]
        parsed = []
        for line in lines:
            m = re.match(r'^([AB])\s*:\s*(.+)', line, re.IGNORECASE)
            if m:
                parsed.append((m.group(1).upper(), m.group(2).strip()))

        if not parsed:
            st.warning("No valid A: / B: lines found. Please check your format.")
        else:
            with st.spinner(f"Generating {len(parsed)} lines of dialogue..."):
                try:
                    tmp_dir = tempfile.mkdtemp()
                    all_segments = build_intro_segments(tmp_dir)

                    progress = st.progress(0, text="Generating voices...")
                    dialogue_clips = []

                    for i, (speaker, text) in enumerate(parsed):
                        clip_path = os.path.join(tmp_dir, f"line_{i}.mp3")
                        v = voice_a if speaker == "A" else voice_b
                        r = rate_a  if speaker == "A" else rate_b
                        p = pitch_a if speaker == "A" else pitch_b
                        asyncio.run(generate_clip(text, v, r, p, clip_path))
                        dialogue_clips.append(clip_path)
                        progress.progress(
                            (i + 1) / len(parsed),
                            text=f"Line {i+1} of {len(parsed)}..."
                        )

                    # Interleave dialogue clips with silence
                    silence_path = os.path.join(tmp_dir, "silence.mp3")
                    generate_silence_mp3(silence_path, pause_ms)
                    for i, clip in enumerate(dialogue_clips):
                        all_segments.append(clip)
                        if i < len(dialogue_clips) - 1:
                            all_segments.append(silence_path)

                    # Final stitch
                    out_path = os.path.join(tmp_dir, "output.mp3")
                    list_path = os.path.join(tmp_dir, "list.txt")
                    with open(list_path, "w") as f:
                        for seg in all_segments:
                            f.write(f"file '{seg}'\n")
                    subprocess.run([
                        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", list_path,
                        "-acodec", "libmp3lame", "-q:a", "4", out_path
                    ], capture_output=True)

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
                        for speaker, text in parsed:
                            name_a = voice_a_label.split("(")[1].split(")")[0]
                            name_b = voice_b_label.split("(")[1].split(")")[0]
                            label = name_a if speaker == "A" else name_b
                            st.markdown(f"**{speaker} — {label}:** {text}")

                except Exception as e:
                    st.error(f"Something went wrong: {e}")

st.divider()
st.caption("Built with Streamlit + Microsoft Edge TTS · Single voice & dialogue modes")
