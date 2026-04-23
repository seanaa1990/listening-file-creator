import streamlit as st
import edge_tts
import asyncio
import tempfile
import os

st.set_page_config(page_title="Listening File Creator", page_icon="🎧", layout="centered")

st.title("🎧 Listening File Creator")
st.write("Paste your text below and download a high-quality MP3 for your students.")

# Voice catalogue — friendly names mapped to edge-tts voice strings
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

STYLES = {
    "Default": None,
    "Slow & Clear (for lower levels)": "slow",
}

# --- Sidebar settings ---
st.sidebar.header("Voice Settings")

voice_label = st.sidebar.selectbox("Voice", list(VOICES.keys()))
voice_id = VOICES[voice_label]

rate = st.sidebar.slider("Speed (%)", min_value=50, max_value=120, value=90, step=5,
                          help="100 = normal speed. Lower = slower. Recommended: 75–90 for EFL students.")
pitch = st.sidebar.slider("Pitch (Hz)", min_value=-10, max_value=10, value=0, step=1,
                           help="0 = natural pitch. Negative = deeper, positive = higher.")

filename = st.sidebar.text_input("Output filename", value="listening-activity-1")

# --- Main area ---
text_input = st.text_area(
    "Your text",
    placeholder="Paste your listening passage here...",
    height=240
)

word_count = len(text_input.split()) if text_input.strip() else 0
char_count = len(text_input)
st.caption(f"{word_count} words · {char_count} characters")

async def generate_audio(text, voice, rate_pct, pitch_hz, out_path):
    rate_str = f"{rate_pct - 100:+d}%"   # e.g. -10% or +5%
    pitch_str = f"{pitch_hz:+d}Hz"
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    await communicate.save(out_path)

if st.button("⏺ Generate MP3", type="primary", use_container_width=True):
    if not text_input.strip():
        st.warning("Please enter some text first.")
    else:
        with st.spinner("Generating audio with Microsoft Neural voices..."):
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tmp_path = tmp.name

                asyncio.run(generate_audio(
                    text_input.strip(),
                    voice_id,
                    rate,
                    pitch,
                    tmp_path
                ))

                with open(tmp_path, "rb") as f:
                    audio_bytes = f.read()

                os.unlink(tmp_path)

                st.audio(audio_bytes, format="audio/mp3")

                st.download_button(
                    label="⬇ Download MP3",
                    data=audio_bytes,
                    file_name=f"{filename.strip() or 'listening-audio'}.mp3",
                    mime="audio/mp3",
                    use_container_width=True
                )
                st.success("Done! Listen above or download the MP3.")

            except Exception as e:
                st.error(f"Something went wrong: {e}")
                st.info("Make sure edge-tts is installed: `pip install edge-tts`")

st.divider()
st.caption("Built with Streamlit + Microsoft Edge TTS (neural voices, free, no API key needed)")
