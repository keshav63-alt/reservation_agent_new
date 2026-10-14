# bot/metrics.py
def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


class SessionMetricsTracker:
    """Accumulates usage tokens and latencies across the call."""

    def __init__(self):
        self.llm_prompt_tokens = 0
        self.llm_completion_tokens = 0
        self.llm_total_tokens = 0
        self.tts_characters = 0
        self.stt_audio_seconds = 0.0

        self.llm_ttfb: list[float] = []
        self.tts_ttfb: list[float] = []

    def record_usage(self, record):
        kind = str(getattr(record, "kind", "")).lower()
        p_tokens = getattr(record, "prompt_tokens", None)
        c_tokens = getattr(record, "completion_tokens", None)
        t_tokens = getattr(record, "total_tokens", None)

        if "llm" in kind or p_tokens is not None or c_tokens is not None:
            if p_tokens:
                self.llm_prompt_tokens += p_tokens
            if c_tokens:
                self.llm_completion_tokens += c_tokens
            if t_tokens:
                self.llm_total_tokens += t_tokens
            elif p_tokens or c_tokens:
                self.llm_total_tokens += (p_tokens or 0) + (c_tokens or 0)

        chars = getattr(record, "characters", None)
        if "tts" in kind or chars is not None:
            if chars:
                self.tts_characters += chars

        audio_secs = getattr(record, "audio_seconds", None)
        if "stt" in kind or audio_secs is not None:
            if audio_secs:
                self.stt_audio_seconds += audio_secs

    def record_latency(self, record):
        processor = str(getattr(record, "processor", "")).lower()
        kind = str(getattr(record, "kind", "")).lower()
        secs = getattr(record, "seconds", 0.0)

        if "llm" in processor or "groq" in processor:
            if "ttfb" in kind:
                self.llm_ttfb.append(secs)
        elif "tts" in processor or "sarvam" in processor:
            if "ttfb" in kind:
                self.tts_ttfb.append(secs)

    def print_summary(self):
        border = "=" * 62
        print(f"\n{border}")
        print("📊 CALL SESSION METRICS & USAGE REPORT")
        print(border)
        print("🔹 LLM (Groq openai/gpt-oss-20b):")
        print(f"   • Prompt Tokens:      {self.llm_prompt_tokens:,}")
        print(f"   • Completion Tokens:  {self.llm_completion_tokens:,}")
        print(f"   • Total Tokens:       {self.llm_total_tokens:,}")
        print(
            f"   • TTFB Latency:       avg {_avg(self.llm_ttfb)}s (calls: {len(self.llm_ttfb)})"
        )
        print("🔹 TTS (Sarvam bulbul:v3):")
        print(f"   • Synthesized Chars:  {self.tts_characters:,}")
        print(
            f"   • TTFB Latency:       avg {_avg(self.tts_ttfb)}s (turns: {len(self.tts_ttfb)})"
        )
        print("🔹 STT (Sarvam Realtime):")
        print(f"   • Audio Transcribed:  {self.stt_audio_seconds:.1f} seconds")
        print(f"{border}\n")
