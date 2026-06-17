# Cilio Unofficial API

Unofficial Python integrations for Cilio.

## Integrations

- `cilio_get_available_times.py` - `get_available_times`.
- `cilio_get_ime_water_heaters.py` - `get_ime_water_heaters`.
- `cilio_submit_presale_questions.py` - `submit_presale_questions`.
- `cilio_schedule_appointment.py` - `schedule_appointment`.

## Usage

Each file exposes a `run(input, context)` entrypoint. The runtime is expected to provide:

- `input`: integration-specific request fields.
- `context["headers"]`: authenticated request headers when required.
- `context["base_url"]`: the platform base URL when overriding the default.

Install dependencies:

```bash
pip install -r requirements.txt
```

## Info

This unofficial API is built by [Integuru.ai](https://integuru.ai/).

For custom requests or hosted authentication, contact richard@taiki.online.

See the [complete list of APIs by Integuru](https://github.com/Integuru-AI/APIs-by-Integuru).
