import getpass
import logging
import os
import configparser
from config.config import config
from utils.encryption import get_password_from_user, load_transcription_key, load_text_key, load_mm_key
from ui.utils import update_status

logger = logging.getLogger(__name__)


def _auto_save_session_key(config_path: str, key: str) -> None:
    """Write the auto-generated session key back to settings.ini immediately."""
    cp = configparser.ConfigParser()
    try:
        cp.read(config_path)
    except configparser.Error:
        pass
    if "OAUTH" not in cp:
        cp["OAUTH"] = {}
    cp["OAUTH"]["SessionSecretKey"] = key
    try:
        with open(config_path, "w") as f:
            cp.write(f)
    except OSError as e:
        logger.warning("Could not persist session key: %s", e)


def get_default_config_path():
    """Returns the platform-specific default config file path."""
    if os.name == "nt":  # Windows
        config_dir = os.path.join(os.environ["APPDATA"], "VOXRAD")
    else:  # Assuming macOS or Linux
        config_dir = os.path.join(os.path.expanduser("~"), ".voxrad")
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)
    return os.path.join(config_dir, "settings.ini")

def load_settings(web_mode: bool = False):
    """Loads settings from the config file, checks for key existence,
       and prompts for password if needed.

    Parameters
    ----------
    web_mode : bool
        When True, skip Tkinter dialogs. API key passwords are read from
        environment variables (VOXRAD_TRANSCRIPTION_PASSWORD, VOXRAD_TEXT_PASSWORD,
        VOXRAD_MM_PASSWORD) or obtained via getpass.getpass() at the terminal.
    """
    config_path = get_default_config_path()
    config.config_path = config_path  # Save config path to config for usage elsewhere
    config_parser = configparser.ConfigParser()
    try:
        config_parser.read(config_path)
    except configparser.Error as e:
        logger.error("Corrupted settings.ini — using defaults. Error: %s", e)
        update_status(
            "settings.ini is corrupted and could not be read. Default settings will be used. "
            "You can reconfigure in Settings."
        )

    if "DEFAULT" in config_parser:
        config.save_directory = config_parser["DEFAULT"].get("WorkingDirectory", os.path.dirname(config_path))
        config.TRANSCRIPTION_BASE_URL = config_parser["DEFAULT"].get("TranscriptionBaseURL", "https://api.groq.com/openai/v1")
        config.SELECTED_TRANSCRIPTION_MODEL = config_parser["DEFAULT"].get("SelectedTranscriptionModel", "whisper-large-v3-turbo")
        config.BASE_URL = config_parser["DEFAULT"].get("TextBaseURL", "https://api.openai.com/v1")
        config.SELECTED_MODEL = config_parser["DEFAULT"].get("SelectedModel", "gpt-4o-mini")
        config.multimodal_pref = config_parser["DEFAULT"].getboolean("MultimodalPref", False)
        config.multimodal_model = config_parser["DEFAULT"].get("MultimodalModel", None)
        config.audio_device = config_parser['DEFAULT'].get('AudioDevice', config.audio_device)
        config.secure_paste_shortcut = config_parser["DEFAULT"].get("SecurePasteShortcut", "ctrl+shift+v")
        config.fhir_export_enabled = config_parser["DEFAULT"].getboolean("FhirExportEnabled", False)
        config.STREAMING_STT_PROVIDER = config_parser["DEFAULT"].get("StreamingSTTProvider", "") or None
    else:
        logger.warning("'DEFAULT' section not found in settings.ini. Using default values.")
        config.save_directory = os.path.dirname(config_path)
        config.TRANSCRIPTION_BASE_URL = "https://api.groq.com/openai/v1"
        config.SELECTED_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"
        config.BASE_URL = "https://api.openai.com/v1"
        config.SELECTED_MODEL = "gpt-4o-mini"

    if "HL7" in config_parser:
        h = config_parser["HL7"]
        config.hl7_export_enabled     = h.getboolean("ExportEnabled", False)
        config.hl7_outbox_path        = h.get("OutboxPath", "")
        config.hl7_sending_facility   = h.get("SendingFacility", "VOXRAD")
        config.hl7_receiving_facility = h.get("ReceivingFacility", "")
        config.hl7_inbox_path         = h.get("InboxPath", "")

    if "OAUTH" in config_parser:
        o = config_parser["OAUTH"]
        config.oauth_redirect_base_url = o.get("RedirectBaseURL", "")
        config.google_client_id        = o.get("GoogleClientID", "")
        config.google_client_secret    = o.get("GoogleClientSecret", "")
        config.microsoft_client_id     = o.get("MicrosoftClientID", "")
        config.microsoft_client_secret = o.get("MicrosoftClientSecret", "")
        config.session_secret_key      = o.get("SessionSecretKey", "")

    # Auto-generate and persist the session secret key if it hasn't been set.
    if not config.session_secret_key:
        import secrets as _secrets
        config.session_secret_key = _secrets.token_hex(32)
        # Persist immediately so it survives restarts.
        _auto_save_session_key(config_path, config.session_secret_key)
        logger.info("Generated and saved new SESSION_SECRET_KEY to settings.ini")

    # Env-var overrides (allow operators to inject secrets without writing to disk)
    config.google_client_id        = os.environ.get("GOOGLE_CLIENT_ID",        config.google_client_id)
    config.google_client_secret    = os.environ.get("GOOGLE_CLIENT_SECRET",    config.google_client_secret)
    config.microsoft_client_id     = os.environ.get("MICROSOFT_CLIENT_ID",     config.microsoft_client_id)
    config.microsoft_client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", config.microsoft_client_secret)
    config.oauth_redirect_base_url = os.environ.get("OAUTH_REDIRECT_BASE_URL", config.oauth_redirect_base_url)
    config.session_secret_key      = os.environ.get("SESSION_SECRET_KEY",      config.session_secret_key)

    # HL7 env-var overrides (for Docker / 12-factor deployments)
    _hl7_enabled_env = os.environ.get("VOXRAD_HL7_ENABLED")
    if _hl7_enabled_env is not None:
        config.hl7_export_enabled = _hl7_enabled_env.strip().lower() in ("1", "true", "yes", "on")
    config.hl7_outbox_path        = os.environ.get("VOXRAD_HL7_OUTBOX",             config.hl7_outbox_path)
    config.hl7_sending_facility   = os.environ.get("VOXRAD_HL7_SENDING_FACILITY",   config.hl7_sending_facility)
    config.hl7_receiving_facility = os.environ.get("VOXRAD_HL7_RECEIVING_FACILITY", config.hl7_receiving_facility)
    config.hl7_inbox_path         = os.environ.get("VOXRAD_HL7_INBOX",              config.hl7_inbox_path)

    if "STYLE" in config_parser:
        s = config_parser["STYLE"]
        config.style_spelling              = s.get("Spelling", config.style_spelling)
        config.style_numerals              = s.get("Numerals", config.style_numerals)
        config.style_measurement_unit      = s.get("MeasurementUnit", config.style_measurement_unit)
        config.style_measurement_separator = s.get("MeasurementSeparator", config.style_measurement_separator)
        try:
            config.style_decimal_precision = int(s.get("DecimalPrecision", config.style_decimal_precision))
        except (TypeError, ValueError):
            pass
        config.style_laterality            = s.get("Laterality", config.style_laterality)
        config.style_impression_style      = s.get("ImpressionStyle", config.style_impression_style)
        config.style_negation_phrasing     = s.get("NegationPhrasing", config.style_negation_phrasing)
        config.style_date_format           = s.get("DateFormat", config.style_date_format)
        config.style_paste_format          = s.get("PasteFormat", config.style_paste_format)

    logger.debug("Using save_directory: %s", config.save_directory)
    logger.debug("Using Transcription Base URL: %s", config.TRANSCRIPTION_BASE_URL)
    logger.debug("Using Text Base URL: %s", config.BASE_URL)
    logger.debug("Using Selected Model for Transcription: %s", config.SELECTED_TRANSCRIPTION_MODEL)
    logger.debug("Using Selected Model: %s", config.SELECTED_MODEL)
    logger.debug("Using Multimodal Pref: %s", config.multimodal_pref)
    logger.debug("Using Multimodal Model: %s", config.multimodal_model)
    logger.debug("Using Secure Paste Shortcut: %s", config.secure_paste_shortcut)

    # ── Web-mode env-var overrides ─────────────────────────────────────────
    # These allow Docker / 12-factor deployments without the desktop key-setup
    # wizard.  Plaintext API key env vars are used as a fallback only when no
    # encrypted key file is present; they are never written to disk.
    if web_mode:
        working_dir_env = os.environ.get("VOXRAD_WORKING_DIR")
        if working_dir_env:
            config.save_directory = working_dir_env
            os.makedirs(working_dir_env, exist_ok=True)
            logger.info("[web] Using VOXRAD_WORKING_DIR: %s", working_dir_env)

        # Base URL + model overrides — essential for cloud deployments where
        # local Whisper / Ollama are not available.
        if os.environ.get("VOXRAD_TRANSCRIPTION_BASE_URL"):
            config.TRANSCRIPTION_BASE_URL = os.environ["VOXRAD_TRANSCRIPTION_BASE_URL"]
            logger.info("[web] Using VOXRAD_TRANSCRIPTION_BASE_URL: %s", config.TRANSCRIPTION_BASE_URL)
        if os.environ.get("VOXRAD_TRANSCRIPTION_MODEL"):
            config.SELECTED_TRANSCRIPTION_MODEL = os.environ["VOXRAD_TRANSCRIPTION_MODEL"]
            logger.info("[web] Using VOXRAD_TRANSCRIPTION_MODEL: %s", config.SELECTED_TRANSCRIPTION_MODEL)
        if os.environ.get("VOXRAD_TEXT_BASE_URL"):
            config.BASE_URL = os.environ["VOXRAD_TEXT_BASE_URL"]
            logger.info("[web] Using VOXRAD_TEXT_BASE_URL: %s", config.BASE_URL)
        if os.environ.get("VOXRAD_TEXT_MODEL"):
            config.SELECTED_MODEL = os.environ["VOXRAD_TEXT_MODEL"]
            logger.info("[web] Using VOXRAD_TEXT_MODEL: %s", config.SELECTED_MODEL)
        if os.environ.get("VOXRAD_STREAMING_STT_PROVIDER"):
            config.STREAMING_STT_PROVIDER = os.environ["VOXRAD_STREAMING_STT_PROVIDER"] or None
            logger.info("[web] Using VOXRAD_STREAMING_STT_PROVIDER: %s", config.STREAMING_STT_PROVIDER)

    config_dir = os.path.dirname(config.config_path)

    def _get_password_web(env_var: str, prompt: str) -> str | None:
        """Get API key password without Tkinter: env var first, then getpass."""
        pw = os.environ.get(env_var)
        if pw:
            logger.info("[web] Using %s from environment.", env_var)
            return pw
        try:
            return getpass.getpass(f"{prompt}: ") or None
        except (EOFError, KeyboardInterrupt):
            return None

    # Transcription Key Handling
    salt_path = os.path.join(config_dir, ".asr_salt")
    transcription_key_path = os.path.join(config_dir, "transcription_key.encrypted")
    if os.path.exists(salt_path) and os.path.exists(transcription_key_path):
        if web_mode:
            password = _get_password_web(
                "VOXRAD_TRANSCRIPTION_PASSWORD",
                "Enter transcription key password",
            )
            if password and not load_transcription_key(transcription_key_path, password):
                logger.error("Incorrect transcription key password.")
        else:
            from tkinter import messagebox
            password = get_password_from_user(
                "Enter your password to unlock the Transcription Model key:", "transcription"
            )
            if password and not load_transcription_key(transcription_key_path, password):
                messagebox.showerror("Error", "Incorrect password for Transcription Model key.")
    else:
        update_status("Kindly save the Transcription key in settings.")

    # Text Key Handling
    salt_path = os.path.join(config_dir, ".text_salt")
    text_key_path = os.path.join(config_dir, "text_key.encrypted")
    if os.path.exists(salt_path) and os.path.exists(text_key_path):
        if web_mode:
            password = _get_password_web(
                "VOXRAD_TEXT_PASSWORD",
                "Enter text model key password",
            )
            if password and not load_text_key(text_key_path, password):
                logger.error("Incorrect text model key password.")
        else:
            from tkinter import messagebox
            password = get_password_from_user(
                "Enter your password to unlock the Text Model key:", "text"
            )
            if password and not load_text_key(text_key_path, password):
                messagebox.showerror("Error", "Incorrect password for Text Model Key.")
    else:
        update_status("Kindly save the Text Model key in settings.")

    # MM Key Handling
    salt_path = os.path.join(config_dir, ".mm_salt")
    mm_key_path = os.path.join(config_dir, "mm_key.encrypted")
    if os.path.exists(salt_path) and os.path.exists(mm_key_path):
        if web_mode:
            password = _get_password_web(
                "VOXRAD_MM_PASSWORD",
                "Enter multimodal model key password",
            )
            if password and not load_mm_key(mm_key_path, password):
                logger.error("Incorrect multimodal model key password.")
        else:
            password = get_password_from_user(
                "Enter your password to unlock the Multimodal Model key:", "mm"
            )
            if password:
                from tkinter import messagebox
                if not load_mm_key(mm_key_path, password):
                    messagebox.showerror("Error", "Incorrect password for Multimodal Model Key.")

    # ── Streaming STT API keys (env-var only, never in settings.ini) ──────
    if web_mode:
        config.DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
        config.ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")

    # ── Plaintext API key env vars (Docker / 12-factor fallback) ──────────
    # Applied after the encrypted-key loading so encrypted keys always win.
    # Only active in web_mode to avoid accidentally bypassing the desktop
    # encryption workflow.
    if web_mode:
        if config.TRANSCRIPTION_API_KEY is None:
            raw = os.environ.get("VOXRAD_TRANSCRIPTION_API_KEY")
            if raw:
                config.TRANSCRIPTION_API_KEY = raw
                logger.info("[web] Using VOXRAD_TRANSCRIPTION_API_KEY from environment.")
        if config.TEXT_API_KEY is None:
            raw = os.environ.get("VOXRAD_TEXT_API_KEY")
            if raw:
                config.TEXT_API_KEY = raw
                logger.info("[web] Using VOXRAD_TEXT_API_KEY from environment.")
        if config.MM_API_KEY is None:
            raw = os.environ.get("VOXRAD_MM_API_KEY")
            if raw:
                config.MM_API_KEY = raw
                logger.info("[web] Using VOXRAD_MM_API_KEY from environment.")


def save_web_settings():
    """Persist non-sensitive web settings to settings.ini.

    Only writes provider/model choices — API keys are never written to disk.
    """
    config_parser = configparser.ConfigParser()
    try:
        config_parser.read(get_default_config_path())
    except configparser.Error:
        pass
    if "DEFAULT" not in config_parser:
        config_parser["DEFAULT"] = {}
    config_parser["DEFAULT"]["TranscriptionBaseURL"] = config.TRANSCRIPTION_BASE_URL or ""
    config_parser["DEFAULT"]["SelectedTranscriptionModel"] = config.SELECTED_TRANSCRIPTION_MODEL or ""
    config_parser["DEFAULT"]["TextBaseURL"] = config.BASE_URL or ""
    config_parser["DEFAULT"]["SelectedModel"] = config.SELECTED_MODEL or ""
    config_parser["DEFAULT"]["FhirExportEnabled"] = str(config.fhir_export_enabled)
    config_parser["DEFAULT"]["StreamingSTTProvider"] = config.STREAMING_STT_PROVIDER or ""
    if "STYLE" not in config_parser:
        config_parser["STYLE"] = {}
    config_parser["STYLE"]["Spelling"]             = config.style_spelling
    config_parser["STYLE"]["Numerals"]             = config.style_numerals
    config_parser["STYLE"]["MeasurementUnit"]      = config.style_measurement_unit
    config_parser["STYLE"]["MeasurementSeparator"] = config.style_measurement_separator
    config_parser["STYLE"]["DecimalPrecision"]     = str(config.style_decimal_precision)
    config_parser["STYLE"]["Laterality"]           = config.style_laterality
    config_parser["STYLE"]["ImpressionStyle"]      = config.style_impression_style
    config_parser["STYLE"]["NegationPhrasing"]     = config.style_negation_phrasing
    config_parser["STYLE"]["DateFormat"]           = config.style_date_format
    config_parser["STYLE"]["PasteFormat"]          = config.style_paste_format
    if "OAUTH" not in config_parser:
        config_parser["OAUTH"] = {}
    config_parser["OAUTH"]["SessionSecretKey"] = config.session_secret_key or ""
    if "HL7" not in config_parser:
        config_parser["HL7"] = {}
    config_parser["HL7"]["ExportEnabled"]    = str(config.hl7_export_enabled)
    config_parser["HL7"]["OutboxPath"]       = config.hl7_outbox_path or ""
    config_parser["HL7"]["SendingFacility"]  = config.hl7_sending_facility or "VOXRAD"
    config_parser["HL7"]["ReceivingFacility"] = config.hl7_receiving_facility or ""
    config_parser["HL7"]["InboxPath"]        = config.hl7_inbox_path or ""
    with open(get_default_config_path(), "w") as f:
        config_parser.write(f)


def save_settings():
    """Saves settings to the config file."""
    config_parser = configparser.ConfigParser()
    config_parser["DEFAULT"] = {
        "WorkingDirectory": str(config.save_directory),  # Convert to string
        'TranscriptionBaseURL': str(config.TRANSCRIPTION_BASE_URL),  # Convert to string
        'SelectedTranscriptionModel': str(config.SELECTED_TRANSCRIPTION_MODEL),  # Convert to string
        "TextBaseURL": str(config.BASE_URL),  # Convert to string
        "SelectedModel": str(config.SELECTED_MODEL),  # Convert to string
        "MultimodalPref": str(config.multimodal_pref),  # Convert to String
        "MultimodalModel": str(config.multimodal_model),
        "AudioDevice": str(config.audio_device),  # Convert to string
        "SecurePasteShortcut": str(config.secure_paste_shortcut),
        "FhirExportEnabled": str(config.fhir_export_enabled),
    }
    with open(get_default_config_path(), "w") as configfile:
        config_parser.write(configfile)
