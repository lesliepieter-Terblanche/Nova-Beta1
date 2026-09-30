"""Silero VAD: a tiny neural network (2 MB, CPU) that tells real speech from fans, typing, music and the TV.

Used by the voice loop to decide when you started and stopped talking, instead of "is it loud?".
Runs with onnxruntime (already installed for the wake word) — no PyTorch needed.
"""
from __future__ import annotations

import numpy as np

URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
RATE = 16000
CHUNK = 512           # 32 ms: the window Silero v5 works on at 16 kHz
CONTEXT = 64          # samples of the previous window it wants in front


def model_file():
    from .config import resolve
    p = resolve("models/vad/silero_vad.onnx")
    if not p.exists() or p.stat().st_size < 100_000:
        import httpx
        p.parent.mkdir(parents=True, exist_ok=True)
        print("[vad] downloading the Silero voice-activity model (2 MB)…")
        r = httpx.get(URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        tmp = p.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(p)
    return p


class SileroVAD:
    def __init__(self, path=None, threshold: float = 0.5):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path or model_file()), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        self.threshold = threshold
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)

    def prob(self, chunk: np.ndarray) -> float:
        """Speech probability for exactly 512 float samples (-1…1) at 16 kHz."""
        x = np.concatenate([self._context, chunk.reshape(1, -1).astype(np.float32)], axis=1)
        out, self._state = self.session.run(None, {"input": x, "state": self._state,
                                                   "sr": np.array(RATE, dtype=np.int64)})
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])

    def speech_in(self, block: np.ndarray) -> float:
        """Highest speech probability in a block of any length (int16 or float). Leftovers carry over."""
        b = block.astype(np.float32)
        if block.dtype == np.int16:
            b = b / 32768.0
        buf = np.concatenate([self._carry, b])
        best, i = 0.0, 0
        while i + CHUNK <= len(buf):
            best = max(best, self.prob(buf[i:i + CHUNK]))
            i += CHUNK
        self._carry = buf[i:]
        return best

    def is_speech(self, block: np.ndarray) -> bool:
        return self.speech_in(block) >= self.threshold


def load(threshold: float = 0.5) -> SileroVAD | None:
    """The VAD, or None (then the voice loop falls back to loudness)."""
    try:
        return SileroVAD(threshold=threshold)
    except Exception as e:
        print(f"[vad] Silero VAD unavailable, using loudness instead: {e}")
        return None
