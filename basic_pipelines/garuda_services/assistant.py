"""Assistant: the Narada voice loop, the chat reply and the AI provider settings.

Moved out of Garuda_web.py (2026-10). Function bodies are unchanged except
that names belonging to Garuda_web are read, and assigned, through `core`: the
live module, handed over once by bind(). Garuda_web imports these functions
back under the same names.
"""
import os
import time

core = None


def bind(module):
    """Called by Garuda_web with itself, before anything here runs."""
    global core
    if core is not None and core is not module:
        # A second copy of Garuda_web (imported under another name) would
        # silently take over the state every function here reads.
        raise RuntimeError(f"{__name__} is already bound to {core.__name__}; "
                           f"refusing a second copy, {module.__name__}")
    core = module


def voice_assistant_loop(stop_event, current_user=None):

    recognizer = core.sr.Recognizer()
    try:
        mic = core.sr.Microphone()
        core.STATE.system.voice_mic_ok, core.STATE.system.voice_mic_detail = True, ""
        core.append_voice_log("Microphone connected.", user_name=current_user)
    except Exception as e:
        core.STATE.system.voice_mic_ok, core.STATE.system.voice_mic_detail = False, str(e)
        core.append_voice_log(f"Error accessing microphone: {e}", user_name=current_user)
        return

    with mic as source:
        recognizer.adjust_for_ambient_noise(source)
        core.append_voice_log("Calibrated for ambient noise.", user_name=current_user)

    while not stop_event.is_set():
        with mic as source:
            core.append_voice_log("Listening...", user_name=current_user)
            try:
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=10)
            except core.sr.WaitTimeoutError:
                continue

        try:
            user_input = recognizer.recognize_google(audio)
            core.append_voice_log(f"You said: {user_input}", user_name=current_user)
        except core.sr.UnknownValueError:
            core.append_voice_log("Could not understand audio.", user_name=current_user)
            continue
        except core.sr.RequestError as e:
            core.append_voice_log(f"Speech recognition error: {e}", user_name=current_user)
            continue

        response = core._assistant_reply(user_input, current_user or "voice", "user")["reply"]

        core.append_voice_response(response, user_name=current_user)
        time.sleep(0.5)


def _voice_turn_logged(user, heard, said):
    core.append_voice_log(f"You said: {heard}", user_name=user)
    core.append_voice_response(said, user_name=user)


def _assistant_reply(msg, user="", role="user", scope="home", voice=False, progress=None):
    """Narada's one brain for chat and voice.

    Phrases the owner taught on the Commands page return their fixed reply
    (they never change anything). Everything else goes to the NIM agent;
    without NIM nothing is changed and Narada says why.
    """
    lower = msg.lower()
    for phrase, resp in core.STATE.config.custom_voice_commands.items():
        if phrase in lower:
            return {"reply": resp, "lane": "custom", "actions": []}
    return core.AGENT.handle(msg, user=user, role=role, scope=scope, voice=voice,
                             progress=progress)


def _ai_configure(fields, actor):
    """Apply AI settings now and persist them to .env for the next start."""
    persist = {}
    if fields.get("nim_api_key"):
        core.NIM_CHAT.configure(api_key=fields["nim_api_key"])
        core.NIM_PLANNER.configure(api_key=fields["nim_api_key"])
        persist["NIM_API_KEY"] = core.NIM_CHAT.api_key
    if fields.get("nim_model") is not None or fields.get("nim_fallback_models") is not None:
        primary = fields.get("nim_model") or os.environ.get("NIM_MODEL", "")
        fallbacks = fields.get("nim_fallback_models")
        if fallbacks is None:
            fallbacks = os.environ.get("NIM_FALLBACK_MODELS", "")
        core.NIM_CHAT.configure(models=core.parse_models(primary, fallbacks))
        persist["NIM_MODEL"] = primary
        persist["NIM_FALLBACK_MODELS"] = fallbacks
    if fields.get("jev_api_key") is not None:
        core.DECISION.jev.api_key = fields["jev_api_key"].strip()
        persist["JEV_API_KEY"] = core.DECISION.jev.api_key
    if fields.get("jev_base_url"):
        core.DECISION.jev.base_url = fields["jev_base_url"].strip().rstrip("/")
        persist["JEV_BASE_URL"] = core.DECISION.jev.base_url
    if fields.get("decision_threshold") is not None:
        core.DECISION.threshold = float(fields["decision_threshold"])
        persist["DECISION_THRESHOLD"] = str(core.DECISION.threshold)
    for name, value in persist.items():
        os.environ[name] = value
    if persist:
        try:
            core._set_env_vars(core.HOME_ENV_PATH, persist)
        except OSError as exc:
            core.log_system_update(f"[HOME] AI settings applied but not saved: {exc}")
    core.log_system_update(f"[HOME] AI settings changed by {actor}: {', '.join(sorted(persist)) or 'none'}")


def _ai_test():
    """One tiny request, so the settings page can say whether the key works."""
    started = time.time()
    try:
        message = core.NIM_CHAT.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                                max_tokens=200, temperature=0, timeout=30)
    except core.NimUnavailable as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "model": core.NIM_CHAT.last_model,
            "latency_s": round(time.time() - started, 2),
            "reply": (message.get("content") or "").strip()[:80]}
