import json
import os
import re
from pathlib import Path

from flask import Flask, render_template_string, request


app = Flask(__name__)
CONFIG_PATH = Path(__file__).resolve().parent / "alpha20_v1" / "config.json"
INTEGER_PATTERN = re.compile(r"^[0-9]+$")

PAGE_TEMPLATE = """
<!doctype html>
<html lang="tr">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Alpha-20 v1 Ayarları</title>
    <style>
      :root {
        color-scheme: dark;
        --background: #0d1117;
        --panel: #161b22;
        --border: #30363d;
        --text: #f0f6fc;
        --muted: #8b949e;
        --accent: #58a6ff;
        --success: #3fb950;
        --warning: #d29922;
      }

      * { box-sizing: border-box; }

      body {
        align-items: center;
        background: radial-gradient(circle at top, #17243a 0, var(--background) 48%);
        color: var(--text);
        display: flex;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
        justify-content: center;
        margin: 0;
        min-height: 100vh;
        padding: 24px;
      }

      main {
        background: color-mix(in srgb, var(--panel) 94%, transparent);
        border: 1px solid var(--border);
        border-radius: 18px;
        box-shadow: 0 24px 70px rgba(0, 0, 0, .35);
        max-width: 620px;
        padding: clamp(24px, 5vw, 44px);
        width: 100%;
      }

      .eyebrow {
        color: var(--accent);
        font-size: .78rem;
        font-weight: 700;
        letter-spacing: .14em;
        margin: 0 0 10px;
        text-transform: uppercase;
      }

      h1 { font-size: clamp(1.65rem, 4vw, 2.25rem); margin: 0 0 12px; }
      .intro, .note { color: var(--muted); line-height: 1.6; }
      .intro { margin: 0 0 26px; }

      .paper-badge {
        background: rgba(63, 185, 80, .13);
        border: 1px solid rgba(63, 185, 80, .45);
        border-radius: 10px;
        color: #7ee787;
        font-weight: 700;
        line-height: 1.5;
        margin-bottom: 28px;
        padding: 13px 15px;
      }

      form { display: grid; gap: 16px; }
      label { font-weight: 700; }

      .controls {
        align-items: center;
        display: grid;
        gap: 18px;
        grid-template-columns: 1fr 112px;
      }

      input[type="range"] {
        accent-color: var(--accent);
        cursor: pointer;
        width: 100%;
      }

      input[type="number"] {
        background: var(--background);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text);
        font-size: 1.1rem;
        padding: 11px 12px;
        width: 100%;
      }

      input:focus-visible, button:focus-visible {
        outline: 3px solid rgba(88, 166, 255, .45);
        outline-offset: 2px;
      }

      .range-labels {
        color: var(--muted);
        display: flex;
        font-size: .8rem;
        justify-content: space-between;
        margin-top: -8px;
      }

      button {
        background: var(--accent);
        border: 0;
        border-radius: 9px;
        color: #08111d;
        cursor: pointer;
        font-size: 1rem;
        font-weight: 800;
        padding: 13px 18px;
      }

      button:hover { filter: brightness(1.08); }

      .message {
        border-radius: 8px;
        line-height: 1.45;
        margin: 0 0 20px;
        padding: 12px 14px;
      }

      .success {
        background: rgba(63, 185, 80, .12);
        border: 1px solid rgba(63, 185, 80, .4);
        color: #7ee787;
      }

      .error {
        background: rgba(248, 81, 73, .12);
        border: 1px solid rgba(248, 81, 73, .45);
        color: #ffaaa5;
      }

      .warning {
        border-left: 3px solid var(--warning);
        color: #e3b341;
        margin: 28px 0 0;
        padding-left: 14px;
      }

      .note { font-size: .9rem; margin: 22px 0 0; }
    </style>
  </head>
  <body>
    <main>
      <p class="eyebrow">Alpha-20 v1</p>
      <h1>Bot Ayarları</h1>
      <p class="intro">Sinyal üretiminde kullanılacak minimum puanı buradan düzenleyin.</p>

      <div class="paper-badge">
        PAPER modu aktif — Bu bot gerçek emir göndermez ve gerçek para ile işlem yapmaz.
      </div>

      {% if message %}
        <p class="message {{ 'success' if message_type == 'success' else 'error' }}" role="status">
          {{ message }}
        </p>
      {% endif %}

      <form method="post">
        <label for="minimum_score">Minimum Sinyal Skoru</label>
        <div class="controls">
          <input
            id="minimum_score_slider"
            type="range"
            min="0"
            max="100"
            step="1"
            value="{{ minimum_score }}"
            aria-label="Minimum Sinyal Skoru kaydırma çubuğu"
          >
          <input
            id="minimum_score"
            name="minimum_score"
            type="number"
            min="0"
            max="100"
            step="1"
            value="{{ minimum_score }}"
            required
          >
        </div>
        <div class="range-labels" aria-hidden="true"><span>0</span><span>100</span></div>
        <button type="submit">Kaydet</button>
      </form>

      <p class="warning">
        Uyarı: Sinyal skorunu değiştirmek kârlılığı garanti etmez. Geçmiş sonuçlar gelecekteki
        performansın göstergesi değildir.
      </p>
      <p class="note">Yalnızca <code>minimum_score</code> ayarı değiştirilir; diğer bot ayarları korunur.</p>
    </main>

    <script>
      const slider = document.getElementById("minimum_score_slider");
      const numberInput = document.getElementById("minimum_score");

      slider.addEventListener("input", () => {
        numberInput.value = slider.value;
      });

      numberInput.addEventListener("input", () => {
        if (numberInput.value !== "" && Number.isInteger(Number(numberInput.value))) {
          const value = Math.min(100, Math.max(0, Number(numberInput.value)));
          slider.value = value;
        }
      });
    </script>
  </body>
</html>
"""


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def save_minimum_score(config: dict, minimum_score: int) -> None:
    updated_config = dict(config)
    updated_config["minimum_score"] = minimum_score
    temporary_path = CONFIG_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as config_file:
        json.dump(updated_config, config_file, ensure_ascii=False, indent=2)
        config_file.write("\n")
    temporary_path.replace(CONFIG_PATH)


def parse_minimum_score(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not INTEGER_PATTERN.fullmatch(value):
        return None
    parsed = int(value)
    return parsed if 0 <= parsed <= 100 else None


@app.route("/", methods=["GET", "POST"])
def index():
    message = None
    message_type = None
    config = load_config()
    minimum_score = config["minimum_score"]

    if request.method == "POST":
        parsed_score = parse_minimum_score(request.form.get("minimum_score"))
        if parsed_score is None:
            message = "Hata: Minimum sinyal skoru 0 ile 100 arasında tam sayı olmalıdır."
            message_type = "error"
        else:
            save_minimum_score(config, parsed_score)
            minimum_score = parsed_score
            message = "Minimum sinyal skoru başarıyla kaydedildi."
            message_type = "success"

    return render_template_string(
        PAGE_TEMPLATE,
        minimum_score=minimum_score,
        message=message,
        message_type=message_type,
    )


@app.get("/favicon.ico")
def favicon():
    return "", 204


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)