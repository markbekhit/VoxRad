
class Config:
    root = None
    save_directory = None
    TEXT_API_KEY = None
    BASE_URL = None
    TRANSCRIPTION_BASE_URL = None
    TRANSCRIPTION_API_KEY = None
    SELECTED_TRANSCRIPTION_MODEL = None
    SELECTED_MODEL = None
    global_md_text_content = ""
    template_dropdown = None
    settings_window = None
    multimodal_pref = False
    multimodal_model = None
    MM_API_KEY = None
    audio_device = None 
    current_encryption_key = None  # To store the key for the encrypted mp3
    current_encrypted_mp3_path = None # To store the encrypted mp3
    secure_paste_shortcut = "ctrl+shift+v"  # Default shortcut
    current_encrypted_report = None  # To store the encrypted report
    current_report_encryption_key = None # To store the key for the encrypted report
    fhir_export_enabled = False  # Export FHIR R4 JSON after each report
    # HL7 v2.4 ORU^R01 file-drop export (for RIS/PACS integration engines)
    hl7_export_enabled = False
    hl7_outbox_path = ""              # defaults to {save_directory}/hl7_outbox when empty
    hl7_sending_facility = "VOXRAD"
    hl7_receiving_facility = ""       # e.g. "NSWHEALTH", "SECTRA", "KESTRAL"
    hl7_inbox_path = ""               # defaults to {save_directory}/hl7_inbox when empty
    # DICOM Basic Text SR file-drop export — alternative to HL7 for PACS
    # that ingest SR directly. Same drop-a-file model as the HL7 outbox.
    dicom_sr_export_enabled = False
    dicom_sr_outbox_path = ""         # defaults to {save_directory}/sr_outbox when empty
    dicom_sr_institution_name = "VOXRAD"
    # MWL (DICOM Modality Worklist) bridge agent
    # The clinic runs a small agent inside their firewall that polls the PACS
    # MWL SCP and POSTs the orders to /api/worklist/push. The token guards that
    # endpoint — when unset, the push endpoint is disabled entirely.
    mwl_agent_token = ""
    # Streaming STT
    STREAMING_STT_PROVIDER = None   # "deepgram" | "assemblyai" | None
    DEEPGRAM_API_KEY = None
    ASSEMBLYAI_API_KEY = None
    # Reporting style preferences (radiologist-facing)
    # Vertebrae are always Arabic — see _build_style_preamble().
    style_spelling = "british"                  # "american" | "british"
    style_numerals = "roman"                    # "roman" | "arabic" (grades & liver segments only)
    style_measurement_unit = "auto"             # "mm" | "cm" | "auto"
    style_measurement_separator = "x"           # "x" | "times" | "by"  (rendered as ×, x, or "by")
    style_decimal_precision = 1                 # 0 | 1 | 2
    style_laterality = "full"                   # "full" (right/left) | "abbrev" (Rt/Lt)
    style_impression_style = "bulleted"         # "bulleted" | "numbered" | "prose"
    style_negation_phrasing = "no_evidence_of"  # "no_evidence_of" | "no_x_identified" | "x_absent"
    style_date_format = "dd_mm_yyyy"            # "dd_mm_yyyy" | "mm_dd_yyyy" | "yyyy_mm_dd"
    style_paste_format = "rich"                 # "rich" | "plain" | "markdown"
    # OAuth / session settings
    oauth_redirect_base_url = ""     # e.g. https://voxrad.example.com
    google_client_id = ""
    google_client_secret = ""
    microsoft_client_id = ""
    microsoft_client_secret = ""
    session_secret_key = ""          # auto-generated and persisted on first run

config = Config()

