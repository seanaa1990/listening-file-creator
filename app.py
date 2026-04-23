import streamlit as st
import edge_tts
import asyncio
import tempfile
import os
import subprocess
import re

st.set_page_config(page_title="Listening File Creator", page_icon="🎧", layout="centered")

st.title("🎧 Listening File Creator")

VOICES = {
    "🇬🇧 British Female (Sonia) — clear, neutral": "en-GB-SoniaNeural",
    "🇬🇧 British Male (Ryan) — warm, natural": "en-GB-RyanNeural",
    "🇺🇸 American Female (Jenny) — friendly": "en-US-JennyNeural",
    "🇺🇸 American Male (Guy) — clear": "en-US-GuyNeural",
    "🇦🇺 Australian Female (Natasha)": "en-AU-NatashaNeural",
    "🇦🇺 Australian Male (William)": "en-AU-WilliamNeural",
    "🇨🇦 Canadian Female (Clara)": "en-CA-ClaraNeural",
    "🇮🇳 Indian Female (Neerja)": "en-IN-NeerjaNeural",
}

async def generate_clip(text, voice, rate_pct, pitch_hz, out_path):
    rate_str = f"{rate_pct - 100:+d}%"
    pitch_str = f"{pitch_hz:+d}Hz"
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    await communicate.save(out_path)

def stitch_clips_ffmpeg(clip_paths, pause_ms, output_path):
    """Stitch MP3 clips together with silence gaps using ffmpeg directly."""
    tmp_dir = tempfile.mkdtemp()
    
    # Build a list of all segments including silence files
    all_segments = []
    silence_path = os.path.join(tmp_dir, "silence.mp3")
    
    # Generate a silence file
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=24000:cl=mono",
        "-t", str(pause_ms / 1000),
        "-q:a", "9", "-acodec", "libmp3lame",
        silence_path
    ], capture_output=True)

    for i, clip in enumerate(clip_paths):
        all_segments.append(clip)
        if i < len(clip_paths) - 1:
            all_segments.append(silence_path)

    # Write concat list file
    list_path = os.path.join(tmp_dir, "list.txt")
    with open(list_path, "w") as f:
        for seg in all_segments:
            f.write(f"file '{seg}'\n")

    # Concatenate
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

if mode == "Single Voice":
    st.sidebar.subheader("Voice")
    voice_label = st.sidebar.selectbox("Voice", list(VOICES.keys()))
    voice_id = VOICES[voice_label]
    rate = st.sidebar.slider("Speed (%)", 50, 120, 90, 5,
                              help="100 = normal. 75–90 recommended for EFL.")
    pitch = st.sidebar.slider("Pitch (Hz)", -10, 10, 0, 1)

else:
    st.sidebar.subheader("Speaker A")
    voice_a_label = st.sidebar.selectbox("Voice A", list(VOICES.keys()), index=0)
    voice_a = VOICES[voice_a_label]
    rate_a = st.sidebar.slider("Speed A (%)", 50, 120, 90, 5)
    pitch_a = st.sidebar.slider("Pitch A (Hz)", -10, 10, 0, 1)

    st.sidebar.subheader("Speaker B")
    voice_b_label = st.sidebar.selectbox("Voice B", list(VOICES.keys()), index=1)
    voice_b = VOICES[voice_b_label]
    rate_b = st.sidebar.slider("Speed B (%)", 50, 120, 90, 5)
    pitch_b = st.sidebar.slider("Pitch B (Hz)", -10, 10, 0, 1)

    st.sidebar.subheader("Dialogue Timing")
    pause_ms = st.sidebar.slider(
        "Pause between turns (ms)", 200, 2000, 600, 100,
        help="Gap between each speaker's line. 500–800ms feels natural."
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
if st.button("⏺ Generate MP3", type="primary", use_container_width=True):
    if not text_input.strip():
        st.warning("Please enter some text first.")

    elif mode == "Single Voice":
        with st.spinner("Generating audio..."):
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tmp_path = tmp.name
                asyncio.run(generate_clip(text_input.strip(), voice_id, rate, pitch, tmp_path))
                with open(tmp_path, "rb") as f:
                    audio_bytes = f.read()
                os.unlink(tmp_path)
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
                    tmp_files = []
                    progress = st.progress(0, text="Generating voices...")

                    for i, (speaker, text) in enumerate(parsed):
                        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                            tmp_path = tmp.name
                        v = voice_a if speaker == "A" else voice_b
                        r = rate_a  if speaker == "A" else rate_b
                        p = pitch_a if speaker == "A" else pitch_b
                        asyncio.run(generate_clip(text, v, r, p, tmp_path))
                        tmp_files.append(tmp_path)
                        progress.progress(
                            (i + 1) / len(parsed),
                            text=f"Line {i+1} of {len(parsed)}..."
                        )

                    # Stitch with ffmpeg
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out:
                        out_path = out.name
                    stitch_clips_ffmpeg(tmp_files, pause_ms, out_path)

                    for f in tmp_files:
                        try: os.unlink(f)
                        except: pass

                    with open(out_path, "rb") as f:
                        audio_bytes = f.read()
                    os.unlink(out_path)

                    st.audio(audio_bytes, format="audio/mp3")
                    st.download_button(
                        "⬇ Download MP3", data=audio_bytes,
                        file_name=f"{filename.strip() or 'dialogue'}.mp3",
                        mime="audio/mp3", use_container_width=True
                    )
                    st.success(f"Done! {len(parsed)}-line dialogue generated.")

                    with st.expander("📄 View transcript"):
                        for speaker, text in parsed:
                            name_a = voice_a_label.split("(")[1].split(")")[0]
                            name_b = voice_b_label.split("(")[1].split(")")[0]
                            label = name_a if speaker == "A" else name_b
                            st.markdown(f"**{speaker} — {label}:** {text}")

                except Exception as e:
                    st.error(f"Something went wrong: {e}")

st.divider()
st.caption("Built with Streamlit + Microsoft Edge TTS · Single voice & dialogue modes")
