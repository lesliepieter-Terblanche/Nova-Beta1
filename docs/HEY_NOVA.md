# Train your own "Hey Nova" wake word (free, ~1 hour, no coding)

Nova ships listening for **"Hey Jarvis"** (a free ready-made model). To make it answer to
**"Hey Nova"**, you train a small custom model once with openWakeWord's free Google Colab notebook.
It generates thousands of synthetic voice samples of the phrase and trains on them — you don't
need to record anything.

## Steps

1. Open the training notebook in Google Colab (sign in with your Google account):
   https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
2. At the top: **Runtime → Change runtime type → T4 GPU** (free) → Save.
3. Find the setting for the target phrase and set it to: `hey nova`
   (Tip: if it doesn't trigger reliably later, retrain with the phrase spelled `hey noh vah`.)
4. **Runtime → Run all.** Accept any prompts. It takes roughly 45–90 minutes. Leave the tab open.
5. When it finishes, download the `.onnx` file it produced (Files panel on the left → right-click → Download).
6. Rename it to `hey_nova.onnx` and put it in Nova's `models` folder.
7. In `config.yaml` change:
   ```yaml
   voice:
     wake_word: models/hey_nova.onnx
   ```
8. Restart Nova (or say "Hey Jarvis, restart yourself" one last time).

## Tuning

- Nova wakes up when you didn't call it → raise `wake_threshold` (e.g. 0.6–0.7).
- Nova misses "Hey Nova" → lower it (e.g. 0.35–0.4), or retrain with more samples.
